"""Shared reader for the Two-Stage dataset in the CSE 412 course archive.

Archive discovery, content checksum, file inventory, ID assignment, CSV row
parsing and observation streaming live here so the exporter (prepare_data.py)
and the database verifier (verify_data.py) cannot drift apart.

Works on Python 3.9+ and needs no third-party packages.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from itertools import compress, count
from pathlib import Path, PurePosixPath
from typing import Optional
from zipfile import ZipFile

# The course zip is dropped into data/ as downloaded; Drive/Canvas add a
# timestamp suffix, so match by prefix. Set TWOSTAGE_ARCHIVE to use another path.
DATA_DIR = Path(__file__).parent
ARCHIVE_PATTERN = 'CSE_412_Semiconductor_Fabrication_Factory*.zip'
ARCHIVE_ENV = 'TWOSTAGE_ARCHIVE'

# Only '<root>/Two-Stage Dataset/<experiment>/<component>.csv' members are data;
# the zip's docx/xlsx/pdf/log.txt files are ignored.
DATASET_DIR = 'Two-Stage Dataset'

# SHA-256 over the dataset CSVs themselves (see dataset_digest), not over the
# zip bytes, so re-zipping or a different download timestamp does not matter.
DATASET_SHA256 = '1a76341613f1b31aac15a05da1d1d56ed49325def4e23b3f28ecaf9241409e90'

MACHINES = frozenset({'a1', 'a2', 'b1', 'b2', 'c1', 'c2', 'd1', 'd2', 'e1', 'e2', 'f1', 'g1'})
SEGMENTS = frozenset({'ab1', 'ab2', 'cd1', 'cd2', 'ee1', 'ee2'})
TRANSDUCERS = frozenset({'tr', 'trd1', 'trd2', 'tri1', 'tri2', 'trl1', 'trl2'})

# Inventory of the reviewed original dataset.
EXPECTED_EXPERIMENTS = 18
EXPECTED_FILES = 451
EXPECTED_COMPONENTS = 28

Time = Optional[str]
Observation = tuple[int, int, Time, str]  # (observation_id, trajectory_id, time, value)


# --- cell classification ----------------------------------------------------

def to_decimal(text: str) -> Optional[Decimal]:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


@lru_cache(maxsize=1 << 16)
def classify(value: str) -> tuple[str, bool]:
    """Return ``(kind, is_nonfinite_number)`` for one stripped, non-empty cell.

    Cached: categorical and message cells repeat heavily, and a failed Decimal
    parse (an exception) is by far the most expensive path.
    """
    if (number := to_decimal(value)) is not None:
        return 'numeric', not number.is_finite()
    if value.startswith('{') and value.endswith('}'):
        return 'message', False
    return 'categorical', False


def column_type(kinds: set[str]) -> str:
    """Collapse the cell kinds seen in one column into a trajectory type."""
    if not kinds:
        return 'empty'
    return next(iter(kinds)) if len(kinds) == 1 else 'mixed'


def _valid_time(text: str) -> bool:
    number = to_decimal(text)
    return number is not None and number.is_finite() and number >= 0


# --- archive ----------------------------------------------------------------

def find_archive() -> Path:
    """Locate the course zip: $TWOSTAGE_ARCHIVE, else the one match in data/."""
    if override := os.environ.get(ARCHIVE_ENV):
        return Path(override)
    matches = sorted(DATA_DIR.glob(ARCHIVE_PATTERN))
    if len(matches) != 1:
        raise FileNotFoundError(
            f'expected exactly one {ARCHIVE_PATTERN} in {DATA_DIR}, found {len(matches)} '
            f'(or set {ARCHIVE_ENV})')
    return matches[0]


def csv_members(archive: ZipFile) -> list[str]:
    names = [n for n in archive.namelist()
             if n.endswith('.csv') and PurePosixPath(n).parent.parent.name == DATASET_DIR]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate archive members')
    return names


def _short(name: str) -> str:
    path = PurePosixPath(name)
    return f'{path.parent.name}/{path.name}'  # '<experiment>/<component>.csv'


def dataset_digest(archive: ZipFile) -> str:
    """SHA-256 of every dataset CSV (name, length, bytes), in sorted order."""
    digest = hashlib.sha256()
    for name in sorted(csv_members(archive), key=_short):
        data = archive.read(name)
        digest.update(f'{_short(name)}\0{len(data)}\0'.encode())
        digest.update(data)
    return digest.hexdigest()


@contextmanager
def _csv_reader(archive: ZipFile, name: str) -> Iterator:
    with archive.open(name) as raw, \
            io.TextIOWrapper(raw, encoding='utf-8-sig', newline='') as text:
        yield csv.reader(text)


def _code_key(code: str) -> tuple[int, ...]:
    return tuple(map(int, code.split('-')))


@dataclass(frozen=True)
class Member:
    """One CSV file: one component of one experiment, many trajectories."""
    name: str                       # path inside the archive
    code: str                       # experiment folder, e.g. '6-6-0-508'
    component: str                  # file stem, e.g. 'tr'
    variables: tuple[str, ...]      # header minus the blank time column
    trajectory_ids: range           # one global ID per variable


@dataclass(frozen=True, eq=False)
class Dataset:
    archive: ZipFile
    codes: tuple[str, ...]
    exp_ids: dict[str, int]
    comp_ids: dict[str, int]
    members: tuple[Member, ...]     # ordered by (experiment, component)

    @classmethod
    def scan(cls, archive: ZipFile) -> Dataset:
        names = csv_members(archive)
        paths = {n: PurePosixPath(n) for n in names}

        codes = sorted({p.parent.name for p in paths.values()}, key=_code_key)
        components = sorted({p.stem for p in paths.values()})
        found = (len(codes), len(names), len(components))
        expected = (EXPECTED_EXPERIMENTS, EXPECTED_FILES, EXPECTED_COMPONENTS)
        if found != expected:
            raise ValueError('Archive inventory differs from the reviewed original: '
                             f'(experiments, files, components) {found} != {expected}')

        exp_ids = {code: i for i, code in enumerate(codes, 1)}
        comp_ids = {name: i for i, name in enumerate(components, 1)}
        if unknown := (MACHINES | SEGMENTS | TRANSDUCERS) - comp_ids.keys():
            raise ValueError(f'Subtype components missing from archive: {sorted(unknown)}')

        names.sort(key=lambda n: (exp_ids[paths[n].parent.name], paths[n].stem))
        members = []
        next_id = 1
        for name in names:
            with _csv_reader(archive, name) as reader:
                header = next(reader, [])
            if header[:1] != [''] or len(set(header)) != len(header) or not all(header[1:]):
                raise ValueError(f'Invalid or duplicate header: {name}')
            variables = tuple(header[1:])
            ids = range(next_id, next_id + len(variables))
            next_id = ids.stop
            members.append(Member(name, paths[name].parent.name, paths[name].stem,
                                  variables, ids))
        return cls(archive, tuple(codes), exp_ids, comp_ids, tuple(members))

    def rows(self, member: Member) -> Iterator[tuple[Time, list[str]]]:
        """Yield ``(time, stripped cells)`` per data row, validating shape and time."""
        width = len(member.variables) + 1
        padding = [''] * (width - 1)
        with _csv_reader(self.archive, member.name) as reader:
            next(reader)  # header, already validated by scan()
            for index, row in enumerate(reader):
                if len(row) == 1:
                    row += padding  # each export ends with a timestamp and no values
                where = f'{member.name}:{reader.line_num}'
                if len(row) != width:
                    raise ValueError(f'{where}: unexpected CSV width {len(row)}')
                time = row[0].strip() or None
                if time is None:
                    if index:  # only the initial-state row may lack a time
                        raise ValueError(f'{where}: unexpected missing logical time')
                elif not _valid_time(time):
                    raise ValueError(f'{where}: invalid logical time')
                yield time, list(map(str.strip, row[1:]))

    def observations(self) -> Iterator[Observation]:
        """Every non-empty cell, numbered in source order."""
        ids = count(1)
        for member in self.members:
            for time, values in self.rows(member):
                # compress() filters out empty cells at C speed.
                for tid, value in compress(zip(member.trajectory_ids, values), values):
                    yield next(ids), tid, time, value

    def column_types(self, member: Member) -> list[str]:
        """Trajectory type of every column in ``member``."""
        observed: list[set[str]] = [set() for _ in member.variables]
        for _time, values in self.rows(member):
            for kinds, value in compress(zip(observed, values), values):
                if len(kinds) < 2:  # two kinds already means 'mixed'
                    kinds.add(classify(value)[0])
        return [column_type(kinds) for kinds in observed]


@contextmanager
def open_dataset(path: Optional[Path] = None) -> Iterator[Dataset]:
    """Open the course zip, verify the dataset checksum, yield the scanned catalog."""
    with ZipFile(path or find_archive()) as archive:
        if (digest := dataset_digest(archive)) != DATASET_SHA256:
            raise ValueError(f'Dataset checksum mismatch: {digest} (expected {DATASET_SHA256}); '
                             'use the unmodified course archive')
        yield Dataset.scan(archive)
