from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable

from audit.capture import initialize_audit_views
from daily.supplements import initialize_supplements
from intake.batches import initialize_batches
from intake.records import initialize_records
from intake.stations import Station, initialize_stations, register
from issue.freeze import initialize_frozen_packages
from issue.work_batch import initialize_work_batches
from receive.posting import initialize_postings
from receive.receipts import initialize_receipts
from receive.transaction import write_transaction


def open_warehouse(path: str | Path) -> sqlite3.Connection:
    """Open an independent ledger with explicit transaction control."""
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
    except BaseException:
        db.close()
        raise
    return db


def initialize_warehouse(db: sqlite3.Connection, stations: Iterable[Station] = ()) -> None:
    with write_transaction(db):
        initialize_stations(db)
        initialize_batches(db)
        initialize_records(db)
        initialize_supplements(db)
        initialize_work_batches(db)
        initialize_frozen_packages(db)
        initialize_receipts(db)
        initialize_postings(db)
        initialize_audit_views(db)
        for station in stations:
            register(db, station.station_id, station.region, station.units)


def _seed_demo(db: sqlite3.Connection, observations: dict) -> None:
    from intake.batches import ingest

    stations = [Station(item['station_id'], item['region'], tuple(item['units'])) for item in observations['stations']]
    initialize_warehouse(db, stations)
    ingest(db, observations['batch_id'], observations['source_id'], observations['rows'])


def _review_demo(path: Path, observations: dict, day: str, review: dict, wire: bytes) -> tuple[dict, dict]:
    from audit.ledger import read
    from daily.supplements import supplement
    from receive.admission import IncompatiblePackage, accept

    acceptance_id = review['acceptance_id']
    with closing(open_warehouse(path)) as db:
        _seed_demo(db, observations)
        supplement(db, f'local-{acceptance_id}', day, '东区', review['supplement_litres'])
        before = {region.region: region.litres for region in read(db, day=day).regions}
        try:
            accepted = accept(db, acceptance_id, wire)
        except IncompatiblePackage:
            accepted = None

    with closing(open_warehouse(path)) as db:
        initialize_warehouse(db)
        current = read(db, day=day)
        regions = {region.region: region for region in current.regions}
        expected_number = f'RC-{acceptance_id}'
        historical = read(db, receipt_number=expected_number)
        observed = {'before_east': before['东区'], 'accepted': accepted is not None,
                    'east_litres': regions['东区'].litres}
        downstream = {'total_litres': current.total_litres, 'west_litres': regions['西区'].litres,
                      'west_missing': regions['西区'].missing_reads,
                      'receipt_number': None if accepted is None else accepted.receipt_number,
                      'historical_litres': None if historical is None else historical.total_litres}
    return observed, downstream


def _run_demo(observations: dict, correction: dict) -> int:
    from issue.freeze import freeze
    from issue.package import encode_package
    from issue.work_batch import correct

    day = correction['day']
    cases = (
        ('v6', {'before_east': 12, 'accepted': True, 'east_litres': 15},
         {'total_litres': 15, 'west_litres': 0, 'west_missing': 1,
          'receipt_number': 'RC-night-compatible', 'historical_litres': 15}),
        ('v7', {'before_east': 17, 'accepted': False, 'east_litres': 17},
         {'total_litres': 17, 'west_litres': 0, 'west_missing': 1,
          'receipt_number': None, 'historical_litres': None}),
    )
    with TemporaryDirectory(prefix='chengpu-demo-') as tmp:
        root = Path(tmp)
        with closing(open_warehouse(root / 'source.sqlite')) as source:
            _seed_demo(source, observations)
            work = correct(source, correction['batch_id'], day, correction['operations'])
            wire = encode_package(freeze(source, work.batch_id))
        actual_cases = [
            _review_demo(root / f'review-{index}.sqlite', observations, day, review, wire)
            for index, review in enumerate(correction['reviews'])
        ]
    if len(actual_cases) != len(cases):
        raise ValueError('the sample requires two independent review ledgers')

    results = []
    for review, (case_id, expected, downstream_expected), (actual, downstream_actual) in zip(
        correction['reviews'], cases, actual_cases
    ):
        record = {
            'case_id': case_id,
            'input': {'day': day, 'observations': observations['rows'], 'operations': correction['operations'],
                      'review': review},
            'expected': expected, 'actual': actual,
            'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
            'code_paths': ['intake/batches.py', 'daily/aggregation.py', 'issue/freeze.py',
                           'receive/eligibility.py', 'receive/admission.py', 'audit/ledger.py'],
            'oracle': '手算东区原读 3+7=10。正式加 3 后，12+3=15 保持核对为假，17+3=20 改变真假。西区原值为零且一条缺读。',
        }
        if actual != expected or downstream_actual != downstream_expected:
            print('DAY_REPLAY_DIVERGENCE ' + json.dumps(record, ensure_ascii=False), file=sys.stderr)
            return 1
        results.append(record)
    for record in results:
        print('POINTWISE_SCENARIO ' + json.dumps(record, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='运行澄浦监测仓的固定交班样例')
    parser.add_argument('command', choices=('demo',))
    parser.add_argument('observations', type=Path)
    parser.add_argument('correction', type=Path)
    args = parser.parse_args(argv)
    observations = json.loads(args.observations.read_text(encoding='utf-8'))
    correction = json.loads(args.correction.read_text(encoding='utf-8'))
    return _run_demo(observations, correction)


if __name__ == '__main__':
    raise SystemExit(main())
