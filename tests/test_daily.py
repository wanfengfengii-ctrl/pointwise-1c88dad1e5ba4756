from audit.ledger import read
from daily.supplements import SupplementError, supplement
from daily.warehouse import initialize_warehouse
from intake.stations import RegistrationError, Station, find_station
from tests import DAY, EAST, WEST, WarehouseCase


class TestDaily(WarehouseCase):
    def test_reopen_keeps_local_supplement(self):
        db = self.warehouse('daily', seed=True)
        entry = supplement(db, 'yard-copy', DAY, EAST, 2)
        self.assertEqual(entry, supplement(db, 'yard-copy', DAY, EAST, 2))
        with self.assertRaises(SupplementError):
            supplement(db, 'yard-copy', DAY, WEST, 2)
        before = read(db, day=DAY)
        self.assertEqual(before.total_litres, 12)
        self.assertEqual(
            [(ref['kind'], ref.get('supplement_id')) for ref in before.regions[0].provenance],
            [('observation', None), ('observation', None), ('supplement', 'yard-copy')],
        )
        db.close()
        reopened = self.warehouse('daily')
        self.assertEqual(read(reopened, day=DAY), before)
        initialize_warehouse(reopened, self.stations)
        self.assertEqual(read(reopened, day=DAY), before)
        self.assertEqual(entry, supplement(reopened, 'yard-copy', DAY, EAST, 2))
        self.assertEqual(read(reopened, day=DAY), before)

    def test_unregistered_region(self):
        db = self.warehouse('unknown-region', seed=True)
        before = read(db, day=DAY)
        with self.assertRaises(SupplementError):
            supplement(db, 'unallocated', DAY, '北区', 2)
        self.assertEqual(read(db, day=DAY), before)

    def test_conflicting_initialization_is_atomic(self):
        db = self.warehouse('initialization', seed=True)
        before = read(db, day=DAY)
        with self.assertRaises(RegistrationError):
            initialize_warehouse(db, [Station('P-N', '北区', ('L',)), Station('P-E', WEST, ('L',))])
        self.assertIsNone(find_station(db, 'P-N'))
        self.assertEqual(find_station(db, 'P-E').region, EAST)
        self.assertEqual(read(db, day=DAY), before)
