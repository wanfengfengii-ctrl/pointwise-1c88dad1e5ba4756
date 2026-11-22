import json
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.ledger import read
from daily.warehouse import initialize_warehouse, open_warehouse
from intake.batches import ingest
from intake.stations import Station
from issue.freeze import freeze
from issue.package import encode_package
from issue.work_batch import correct

DAY = '2026-11-16'
EAST = '东区'
WEST = '西区'
SAMPLES = Path(__file__).resolve().parent.parent / 'samples'


def load_sample(name):
    return json.loads((SAMPLES / name).read_text(encoding='utf-8'))


class WarehouseCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='chengpu-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.observations = load_sample('observations.json')
        self.correction = load_sample('correction.json')
        self.stations = tuple(
            Station(item['station_id'], item['region'], tuple(item['units']))
            for item in self.observations['stations']
        )

    def warehouse(self, name, *, seed=False, stations=None):
        db = open_warehouse(self.root / f'{name}.sqlite')
        self.addCleanup(db.close)
        initialize_warehouse(db, self.stations if stations is None else stations)
        if seed:
            self.seed_day(db)
        return db

    def seed_day(self, db):
        fixture = self.observations
        return ingest(db, fixture['batch_id'], fixture['source_id'], fixture['rows'])

    def issue(self, operations=None, *, file='source', batch_id=None):
        operations = self.correction['operations'] if operations is None else operations
        batch_id = self.correction['batch_id'] if batch_id is None else batch_id
        with closing(self.warehouse(file, seed=True)) as source:
            correct(source, batch_id, DAY, operations)
            return encode_package(freeze(source, batch_id))

    def regions(self, db, *, day=DAY):
        return {region.region: region for region in read(db, day=day).regions}


def add(litres, region=EAST):
    return {'op': 'add', 'region': region, 'litres': litres}


def check(name, target, region=EAST):
    return {'op': 'check', 'region': region, 'name': name, 'target': target}
