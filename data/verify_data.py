#!/usr/bin/env python3
"""Check PostgreSQL values and IDs against the original CSV archive."""

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import ZipFile


ARCHIVE = Path(__file__).with_name('Two-Stage_Dataset.zip')
ORIGINAL_SHA256 = '05e240495b967558fdbfea152019bd0c569450b82562c60e5f7f4304f9a979cc'


def canonical_time(value):
    """Normalize decimal scale for the source/database comparison."""
    if value is None or value == '':
        return None
    number = Decimal(value)
    if not number.is_finite() or number < 0:
        raise ValueError(f'Invalid logical time: {value!r}')
    fixed = format(number, 'f')
    if '.' in fixed:
        fixed = fixed.rstrip('0').rstrip('.')
    return '0' if number == 0 else fixed


def digest_record(digest, observation_id, trajectory_id, time, value):
    record = [observation_id, trajectory_id, canonical_time(time), value]
    digest.update(json.dumps(record, separators=(',', ':')).encode('utf-8'))
    digest.update(b'\n')


def database_rows(database, query):
    """Read PostgreSQL COPY output as CSV rows."""
    command = [
        'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '--dbname', database,
        '--command', f'COPY ({query}) TO STDOUT WITH (FORMAT CSV)',
    ]
    with subprocess.Popen(command, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True,
                          encoding='utf-8') as process:
        try:
            yield from csv.reader(process.stdout)
        except BaseException:
            process.terminate()
            raise
        finally:
            process.stdout.close()
        error = process.stderr.read()
        code = process.wait()
        if code != 0:
            raise RuntimeError(f'psql exited {code}: {error.strip()}')


def verify(database):
    archive_digest = hashlib.sha256()
    with ARCHIVE.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            archive_digest.update(block)
    actual_archive_digest = archive_digest.hexdigest()
    if actual_archive_digest != ORIGINAL_SHA256:
        raise ValueError('Original dataset archive checksum does not match SourceForge')

    actual_catalog = {}
    query = '''SELECT e.experiment_id, e.experiment_code, c.component_name,
                      t.variable_name, t.trajectory_id
               FROM public.trajectory t
               JOIN public.experiment e USING (experiment_id)
               JOIN public.component c USING (component_id)'''
    for row in database_rows(database, query):
        eid, code, component, variable, tid = row
        key = code, component, variable
        if key in actual_catalog:
            raise ValueError(f'Duplicate database trajectory: {key!r}')
        actual_catalog[key] = int(eid), int(tid)

    expected_digest = hashlib.sha256()
    expected_catalog = set()
    observation_id = 0
    trajectory_id = 0
    blank_time_values = 0
    with ZipFile(ARCHIVE) as archive:
        members = [member for member in archive.namelist()
                   if member.endswith('.csv')]
        if len(members) != len(set(members)):
            raise ValueError('Duplicate original CSV archive entries')
        members.sort(key=lambda member: (
            tuple(int(token) for token in member.split('/')[1].split('-')),
            Path(member).stem,
        ))
        codes = sorted({member.split('/')[1] for member in members},
                       key=lambda code: tuple(int(token) for token in code.split('-')))
        experiment_ids = {code: index for index, code in enumerate(codes, 1)}

        for member in members:
            code = member.split('/')[1]
            component = Path(member).stem
            with archive.open(member) as binary:
                rows = csv.reader(io.TextIOWrapper(binary, encoding='utf-8-sig',
                                                   newline=''))
                header = next(rows)
                if header[0] != '' or len(header) != len(set(header)):
                    raise ValueError(f'Invalid source header: {member}')
                ids = []
                for variable in header[1:]:
                    trajectory_id += 1
                    key = code, component, variable
                    expected = experiment_ids[code], trajectory_id
                    if actual_catalog.get(key) != expected:
                        raise ValueError(f'Trajectory identity mismatch: {key!r}')
                    expected_catalog.add(key)
                    ids.append(trajectory_id)

                for row_number, row in enumerate(rows, 2):
                    if len(row) == 1:
                        # The final timestamp has no observation values.
                        canonical_time(row[0])
                        continue
                    if len(row) != len(header):
                        raise ValueError(f'Invalid source CSV width: {member}:{row_number}')
                    source_time = row[0].strip() or None
                    if source_time is None and row_number != 2:
                        raise ValueError(f'Unexpected blank source time: {member}:{row_number}')
                    for tid, cell in zip(ids, row[1:]):
                        value = cell.strip()
                        if value == '':
                            continue
                        observation_id += 1
                        if source_time is None:
                            blank_time_values += 1
                        digest_record(expected_digest, observation_id, tid,
                                      source_time, value)

    if set(actual_catalog) != expected_catalog:
        raise ValueError('PostgreSQL trajectory inventory differs from the original CSV headers')

    actual_digest = hashlib.sha256()
    database_count = 0
    query = '''SELECT observation_id, trajectory_id, logical_time, value
               FROM public.observation ORDER BY observation_id'''
    for row in database_rows(database, query):
        oid, tid, time, value = row
        database_count += 1
        if int(oid) != database_count:
            raise ValueError(f'Observation identity mismatch at row {database_count}')
        digest_record(actual_digest, int(oid), int(tid), time or None, value)

    source_hash = expected_digest.hexdigest()
    database_hash = actual_digest.hexdigest()
    matches = database_count == observation_id and source_hash == database_hash
    result = {
        'database': database,
        'archive_sha256': actual_archive_digest,
        'csv_files': len(members),
        'experiments': len(codes),
        'trajectories_checked': len(expected_catalog),
        'source_observations': observation_id,
        'database_observations': database_count,
        'source_observations_without_time': blank_time_values,
        'source_sha256': source_hash,
        'database_sha256': database_hash,
        'sha256_match': matches,
    }
    print(json.dumps(result, indent=2))
    if not matches:
        raise ValueError('Original source and PostgreSQL observation streams differ')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', nargs='?', default='cse412_team19')
    args = parser.parse_args()
    try:
        verify(args.database)
    except (OSError, ValueError, RuntimeError, InvalidOperation) as error:
        print(f'Verification failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
