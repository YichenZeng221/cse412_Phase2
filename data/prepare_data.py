#!/usr/bin/env python3
r"""Export dataset CSVs for PostgreSQL COPY."""

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ARCHIVE = Path(__file__).with_name('Two-Stage_Dataset.zip')
SOURCE_SHA256 = '05e240495b967558fdbfea152019bd0c569450b82562c60e5f7f4304f9a979cc'
MACHINES = {'a1', 'a2', 'b1', 'b2', 'c1', 'c2', 'd1', 'd2', 'e1', 'e2', 'f1', 'g1'}
SEGMENTS = {'ab1', 'ab2', 'cd1', 'cd2', 'ee1', 'ee2'}
TRANSDUCERS = {'tr', 'trd1', 'trd2', 'tri1', 'tri2', 'trl1', 'trl2'}
TABLES = ('experiment', 'component', 'stage', 'machine', 'segment', 'transducer',
          'trajectory', 'observation')


def numeric(value):
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def cell_kind(value):
    if numeric(value) is not None:
        return 'numeric'
    if value.startswith('{') and value.endswith('}'):
        return 'message'
    return 'categorical'


def source_rows(archive, member, header):
    with archive.open(member) as stream:
        reader = csv.reader(io.TextIOWrapper(stream, encoding='utf-8-sig', newline=''))
        next(reader)
        for row_number, row in enumerate(reader, 2):
            # Each export ends with a timestamp and no values.
            if len(row) == 1:
                row += [''] * (len(header) - 1)
            if len(row) != len(header):
                raise ValueError(f'{member}:{row_number}: unexpected CSV width {len(row)}')
            time = row[0].strip() or None
            if time is not None:
                number = numeric(time)
                if number is None or not number.is_finite() or number < 0:
                    raise ValueError(f'{member}:{row_number}: invalid logical time')
            elif row_number != 2:
                raise ValueError(f'{member}:{row_number}: unexpected missing logical time')
            yield time, [v.strip() for v in row[1:]]


def inventory(archive):
    files = [n for n in archive.namelist()
             if n.startswith('Two-Stage Dataset/') and n.endswith('.csv')]
    if len(files) != len(set(files)):
        raise ValueError('Duplicate archive members')
    codes = sorted({PurePosixPath(n).parent.name for n in files},
                   key=lambda c: tuple(map(int, c.split('-'))))
    components = sorted({PurePosixPath(n).stem for n in files})
    exp_ids = {code: i for i, code in enumerate(codes, 1)}
    comp_ids = {name: i for i, name in enumerate(components, 1)}
    files.sort(key=lambda n: (exp_ids[PurePosixPath(n).parent.name], PurePosixPath(n).stem))
    catalog = []
    trajectory_id = 0
    for member in files:
        with archive.open(member) as stream:
            header = next(csv.reader(io.TextIOWrapper(stream, encoding='utf-8-sig')))
        if header[0] != '' or len(set(header)) != len(header) or any(not h for h in header[1:]):
            raise ValueError(f'Invalid or duplicate header: {member}')
        ids = list(range(trajectory_id + 1, trajectory_id + len(header)))
        trajectory_id += len(header) - 1
        catalog.append((member, header, ids))
    if len(codes) != 18 or len(files) != 451 or len(components) != 28:
        raise ValueError('Archive inventory differs from the reviewed original')
    return codes, exp_ids, comp_ids, catalog


def export(table, archive, codes, exp_ids, comp_ids, catalog):
    out = csv.writer(sys.stdout, lineterminator='\n')
    if table == 'experiment':
        out.writerow(('experiment_id', 'experiment_code', 'pa_count', 'pb_count', 'tw_count', 'simulation_steps'))
        for code in codes:
            pa, pb, tw, _undocumented_label = map(int, code.split('-'))
            # Leave simulation_steps NULL because the fourth folder token is undocumented.
            out.writerow((exp_ids[code], code, pa, pb, tw, None))
    elif table == 'component':
        out.writerow(('component_id', 'component_name'))
        out.writerows((i, name) for name, i in comp_ids.items())
    elif table == 'stage':
        out.writerow(('stage_id', 'stage_number', 'component_id'))
        # No stage-root component is listed, so the optional FK stays NULL.
        out.writerows(((1, 1, None), (2, 2, None)))
    elif table in ('machine', 'segment', 'transducer'):
        out.writerow(('component_id',))
        names = {'machine': MACHINES, 'segment': SEGMENTS, 'transducer': TRANSDUCERS}[table]
        out.writerows((comp_ids[name],) for name in sorted(names))
    elif table == 'trajectory':
        out.writerow(('trajectory_id', 'experiment_id', 'component_id', 'trajectory_type', 'variable_name'))
        for member, header, ids in catalog:
            kinds = [set() for _ in ids]
            for _time, values in source_rows(archive, member, header):
                for index, value in enumerate(values):
                    if value:
                        kinds[index].add(cell_kind(value))
            for tid, variable, observed in zip(ids, header[1:], kinds):
                kind = next(iter(observed)) if len(observed) == 1 else ('mixed' if observed else 'empty')
                out.writerow((tid, exp_ids[PurePosixPath(member).parent.name],
                              comp_ids[PurePosixPath(member).stem], kind, variable))
    elif table == 'observation':
        out.writerow(('observation_id', 'trajectory_id', 'logical_time', 'value'))
        oid = 0
        for member, header, ids in catalog:
            for time, values in source_rows(archive, member, header):
                for tid, value in zip(ids, values):
                    if value:
                        oid += 1
                        out.writerow((oid, tid, time, value))


def audit(archive, codes, exp_ids, comp_ids, catalog):
    counts = Counter()
    types = Counter()
    experiments = {code: {'experiment_id': exp_ids[code], 'csv_files': 0,
                          'raw_rows': 0, 'observations': 0} for code in codes}
    for member, header, ids in catalog:
        code = PurePosixPath(member).parent.name
        item = experiments[code]
        item['csv_files'] += 1
        kinds = [set() for _ in ids]
        seen = [set() for _ in ids]
        counts['trajectories'] += len(ids)
        for time, values in source_rows(archive, member, header):
            counts['raw_rows'] += 1
            item['raw_rows'] += 1
            if time is None:
                counts['raw_rows_without_time'] += 1
            if not any(values):
                counts['rows_without_values'] += 1
            for index, value in enumerate(values):
                counts['rectangular_value_slots'] += 1
                if not value:
                    counts['empty_value_slots_omitted'] += 1
                    continue
                counts['observations'] += 1
                item['observations'] += 1
                if time is None:
                    counts['observations_without_time'] += 1
                key = time, value
                if key in seen[index]:
                    counts['repeated_trajectory_time_value_retained'] += 1
                seen[index].add(key)
                kinds[index].add(cell_kind(value))
                dec = numeric(value)
                if dec is not None and not dec.is_finite():
                    counts['nonfinite_value_tokens_retained'] += 1
        for observed in kinds:
            types[next(iter(observed)) if len(observed) == 1 else ('mixed' if observed else 'empty')] += 1
    result = {
        'archive_sha256': SOURCE_SHA256,
        'csv_files': len(catalog), 'experiments': experiments,
        'table_counts': {'experiment': len(codes), 'component': len(comp_ids), 'stage': 2,
                         'machine': len(MACHINES), 'segment': len(SEGMENTS),
                         'transducer': len(TRANSDUCERS), 'trajectory': counts['trajectories'],
                         'observation': counts['observations']},
        'source_profile': dict(counts), 'trajectory_types': dict(types),
        'unclassified_base_components': sorted(set(comp_ids) - MACHINES - SEGMENTS - TRANSDUCERS),
        'missing_source_files': ['12-18-6-1541/trd1.csv', '6-6-0-508/ee2.csv'],
        'normalization': 'Trim outer cell whitespace; omit empty value cells; preserve blank initial time as NULL; retain repeated events; no interpolation or fabricated values.',
        'simulation_steps': 'NULL: fourth experiment-folder token has no documented definition.',
        'stage_component_id': 'NULL: no single stage-root component is identified; literal optional ER mapping retained.'
    }
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('table', choices=(*TABLES, 'audit'))
    args = parser.parse_args()
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f'Original archive checksum mismatch: {digest}')
    with ZipFile(ARCHIVE) as archive:
        args_tuple = (archive, *inventory(archive))
        if args.table == 'audit':
            audit(*args_tuple)
        else:
            export(args.table, *args_tuple)


if __name__ == '__main__':
    main()
