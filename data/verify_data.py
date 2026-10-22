#!/usr/bin/env python3
"""Check PostgreSQL values and IDs against the original CSV archive."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from itertools import zip_longest

from twostage import DATASET_SHA256, Dataset, Observation, open_dataset

_encode = json.JSONEncoder(separators=(',', ':')).encode

CATALOG_QUERY = '''SELECT e.experiment_id, e.experiment_code, c.component_name,
                          t.variable_name, t.trajectory_id
                   FROM public.trajectory t
                   JOIN public.experiment e USING (experiment_id)
                   JOIN public.component c USING (component_id)'''
OBSERVATION_QUERY = '''SELECT observation_id, trajectory_id, logical_time, value
                       FROM public.observation ORDER BY observation_id'''

Key = tuple[str, str, str]  # (experiment_code, component_name, variable_name)


@lru_cache(maxsize=4096)  # consecutive observations of a row share one time
def canonical_time(value: str | None) -> str | None:
    """Normalize decimal scale for the source/database comparison."""
    if not value:
        return None
    number = Decimal(value)
    if not number.is_finite() or number < 0:
        raise ValueError(f'Invalid logical time: {value!r}')
    if number == 0:
        return '0'
    fixed = format(number, 'f')
    return fixed.rstrip('0').rstrip('.') if '.' in fixed else fixed


def canonical(records: Iterable[Observation]) -> Iterator[Observation]:
    for oid, tid, time, value in records:
        yield oid, tid, canonical_time(time), value


@dataclass
class StreamSummary:
    count: int = 0
    blank_time: int = 0
    sha256: str = ''


def summarize(records: Iterable[Observation]) -> StreamSummary:
    """Fingerprint a canonical observation stream."""
    digest = hashlib.sha256()
    summary = StreamSummary()
    for record in records:
        summary.count += 1
        summary.blank_time += record[2] is None
        digest.update(f'{_encode(record)}\n'.encode())
    summary.sha256 = digest.hexdigest()
    return summary


def first_difference(source: Iterable[Observation], database: Iterable[Observation]) -> str:
    for n, (src, db) in enumerate(zip_longest(source, database), 1):
        if src != db:
            return f'first difference at record {n}: source={src!r} database={db!r}'
    return 'no record-level difference found'


def copy_rows(database: str, query: str) -> Iterator[list[str]]:
    """Stream a query's result from psql as CSV rows."""
    command = ['psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '--dbname', database,
               '--command', f'COPY ({query}) TO STDOUT WITH (FORMAT CSV)']
    # stderr goes to a file, not a pipe: an undrained pipe can deadlock psql.
    with tempfile.TemporaryFile() as stderr, \
            subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr) as process:
        try:
            # newline='' keeps embedded \r and \r\n intact, matching the source CSV.
            with io.TextIOWrapper(process.stdout, encoding='utf-8', newline='') as text:
                yield from csv.reader(text)
        except BaseException:
            process.terminate()
            raise
        if code := process.wait():
            stderr.seek(0)
            message = stderr.read().decode(errors='replace').strip()
            raise RuntimeError(f'psql exited {code}: {message}')


def database_catalog(database: str) -> dict[Key, tuple[int, int]]:
    catalog: dict[Key, tuple[int, int]] = {}
    for eid, code, component, variable, tid in copy_rows(database, CATALOG_QUERY):
        key = code, component, variable
        if key in catalog:
            raise ValueError(f'Duplicate database trajectory: {key!r}')
        catalog[key] = int(eid), int(tid)
    return catalog


def source_catalog(ds: Dataset) -> dict[Key, tuple[int, int]]:
    return {(m.code, m.component, variable): (ds.exp_ids[m.code], tid)
            for m in ds.members
            for variable, tid in zip(m.variables, m.trajectory_ids)}


def check_catalog(expected: dict[Key, tuple[int, int]],
                  actual: dict[Key, tuple[int, int]]) -> None:
    if expected == actual:
        return
    missing = expected.keys() - actual.keys()
    extra = actual.keys() - expected.keys()
    changed = [k for k in expected.keys() & actual.keys() if expected[k] != actual[k]]
    sample = sorted(missing or extra or changed)[:3]
    raise ValueError('PostgreSQL trajectory inventory differs from the original CSV '
                     f'headers: {len(missing)} missing, {len(extra)} unexpected, '
                     f'{len(changed)} with different IDs; e.g. {sample}')


def database_observations(database: str) -> Iterator[Observation]:
    for oid, tid, time, value in copy_rows(database, OBSERVATION_QUERY):
        yield int(oid), int(tid), time, value  # NULL time arrives as ''


def verify(database: str) -> None:
    with open_dataset() as ds:
        expected = source_catalog(ds)
        check_catalog(expected, database_catalog(database))

        source = summarize(canonical(ds.observations()))
        actual = summarize(canonical(database_observations(database)))
        matches = (source.count, source.sha256) == (actual.count, actual.sha256)
        print(json.dumps({
            'database': database,
            'dataset_sha256': DATASET_SHA256,
            'csv_files': len(ds.members),
            'experiments': len(ds.codes),
            'trajectories_checked': len(expected),
            'source_observations': source.count,
            'database_observations': actual.count,
            'source_observations_without_time': source.blank_time,
            'source_sha256': source.sha256,
            'database_sha256': actual.sha256,
            'sha256_match': matches,
        }, indent=2))
        if not matches:
            detail = first_difference(canonical(ds.observations()),
                                      canonical(database_observations(database)))
            raise ValueError(f'Original source and PostgreSQL observation streams differ ({detail})')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', nargs='?', default='cse412_team19')
    args = parser.parse_args(argv)
    try:
        verify(args.database)
    except (OSError, ValueError, RuntimeError, InvalidOperation, csv.Error) as error:
        print(f'Verification failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
