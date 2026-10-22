#!/usr/bin/env python3
"""Export dataset CSVs for PostgreSQL COPY."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from itertools import compress

from twostage import (MACHINES, SEGMENTS, DATASET_SHA256, TRANSDUCERS, Dataset,
                      classify, column_type, open_dataset)

# Reported by the audit only; these files are absent from the original archive.
KNOWN_MISSING_FILES = ['12-18-6-1541/trd1.csv', '6-6-0-508/ee2.csv']

Exporter = Callable[[Dataset], Iterable[tuple]]
EXPORTERS: dict[str, Exporter] = {}


def exporter(table: str) -> Callable[[Exporter], Exporter]:
    def register(func: Exporter) -> Exporter:
        EXPORTERS[table] = func
        return func
    return register


@exporter('experiment')
def experiment_rows(ds: Dataset) -> Iterator[tuple]:
    yield ('experiment_id', 'experiment_code', 'pa_count', 'pb_count', 'tw_count',
           'simulation_steps')
    for code in ds.codes:
        pa, pb, tw, _undocumented_label = map(int, code.split('-'))
        # simulation_steps stays NULL because the fourth folder token is undocumented.
        yield ds.exp_ids[code], code, pa, pb, tw, None


@exporter('component')
def component_rows(ds: Dataset) -> Iterator[tuple]:
    yield ('component_id', 'component_name')
    yield from ((i, name) for name, i in ds.comp_ids.items())


@exporter('stage')
def stage_rows(ds: Dataset) -> Iterator[tuple]:
    yield ('stage_id', 'stage_number', 'component_id')
    # No stage-root component is listed, so the optional FK stays NULL.
    yield 1, 1, None
    yield 2, 2, None


def subtype_exporter(names: frozenset[str]) -> Exporter:
    def rows(ds: Dataset) -> Iterator[tuple]:
        yield ('component_id',)
        yield from ((ds.comp_ids[name],) for name in sorted(names))
    return rows


EXPORTERS.update(machine=subtype_exporter(MACHINES),
                 segment=subtype_exporter(SEGMENTS),
                 transducer=subtype_exporter(TRANSDUCERS))


@exporter('trajectory')
def trajectory_rows(ds: Dataset) -> Iterator[tuple]:
    yield ('trajectory_id', 'experiment_id', 'component_id', 'trajectory_type',
           'variable_name')
    for member in ds.members:
        exp_id = ds.exp_ids[member.code]
        comp_id = ds.comp_ids[member.component]
        for tid, kind, variable in zip(member.trajectory_ids, ds.column_types(member),
                                       member.variables):
            yield tid, exp_id, comp_id, kind, variable


@exporter('observation')
def observation_rows(ds: Dataset) -> Iterator[tuple]:
    yield ('observation_id', 'trajectory_id', 'logical_time', 'value')
    yield from ds.observations()


def audit(ds: Dataset) -> dict:
    totals: Counter[str] = Counter()
    types: Counter[str] = Counter()
    experiments = {code: {'experiment_id': ds.exp_ids[code], 'csv_files': 0,
                          'raw_rows': 0, 'observations': 0} for code in ds.codes}

    for member in ds.members:
        width = len(member.variables)
        counts: Counter[str] = Counter()
        observed = [set() for _ in range(width)]
        seen = [set() for _ in range(width)]

        for time, values in ds.rows(member):
            empty = values.count('')
            filled = width - empty
            counts['raw_rows'] += 1
            counts['rectangular_value_slots'] += width
            if time is None:
                counts['raw_rows_without_time'] += 1
            if not filled:
                counts['rows_without_values'] += 1
            if empty:
                counts['empty_value_slots_omitted'] += empty
            if filled:
                counts['observations'] += filled
                if time is None:
                    counts['observations_without_time'] += filled
            for index, value in compress(enumerate(values), values):
                key = time, value
                if key in seen[index]:
                    counts['repeated_trajectory_time_value_retained'] += 1
                else:
                    seen[index].add(key)
                kind, nonfinite = classify(value)
                observed[index].add(kind)
                if nonfinite:
                    counts['nonfinite_value_tokens_retained'] += 1

        totals['trajectories'] += width
        totals.update(counts)
        types.update(map(column_type, observed))
        item = experiments[member.code]
        item['csv_files'] += 1
        item['raw_rows'] += counts['raw_rows']
        item['observations'] += counts['observations']

    return {
        'dataset_sha256': DATASET_SHA256,
        'csv_files': len(ds.members),
        'experiments': experiments,
        'table_counts': {'experiment': len(ds.codes), 'component': len(ds.comp_ids),
                         'stage': 2, 'machine': len(MACHINES), 'segment': len(SEGMENTS),
                         'transducer': len(TRANSDUCERS),
                         'trajectory': totals['trajectories'],
                         'observation': totals['observations']},
        'source_profile': dict(totals),
        'trajectory_types': dict(types),
        'unclassified_base_components':
            sorted(ds.comp_ids.keys() - MACHINES - SEGMENTS - TRANSDUCERS),
        'missing_source_files': KNOWN_MISSING_FILES,
        'normalization': 'Trim outer cell whitespace; omit empty value cells; preserve blank initial time as NULL; retain repeated events; no interpolation or fabricated values.',
        'simulation_steps': 'NULL: fourth experiment-folder token has no documented definition.',
        'stage_component_id': 'NULL: no single stage-root component is identified; literal optional ER mapping retained.',
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('table', choices=(*EXPORTERS, 'audit'))
    args = parser.parse_args(argv)
    # Pin the byte stream psql \copy reads, whatever the locale says.
    sys.stdout.reconfigure(encoding='utf-8', newline='')
    try:
        with open_dataset() as ds:
            if args.table == 'audit':
                json.dump(audit(ds), sys.stdout, indent=2)
                sys.stdout.write('\n')
            else:
                csv.writer(sys.stdout, lineterminator='\n').writerows(
                    EXPORTERS[args.table](ds))
    except (OSError, ValueError, csv.Error) as error:
        print(f'{args.table}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
