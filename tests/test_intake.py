from audit.ledger import read
from intake.batches import IngestError, find_batch, ingest
from intake.stations import RegistrationError, find_station, register
from tests import DAY, EAST, WEST, WarehouseCase


class TestIntake(WarehouseCase):
    def test_units_and_missing(self):
        db = self.warehouse('intake')
        east, _, missing, zero = self.observations['rows']
        rows = [east, missing, zero]
        first = ingest(db, 'opening', 'first-report', rows)
        self.assertEqual(first, ingest(db, 'opening', 'first-report', rows))
        view = read(db, day=DAY)
        self.assertEqual(view.total_litres, 3)
        self.assertEqual([region.region for region in view.regions], [EAST, WEST])
        east_book, west_book = view.regions
        self.assertEqual((east_book.litres, east_book.valid_reads, east_book.missing_reads), (3, 1, 0))
        self.assertEqual((west_book.litres, west_book.valid_reads, west_book.missing_reads), (0, 1, 1))
        self.assertEqual(len(east_book.provenance), 1)
        self.assertEqual((east_book.provenance[0]['raw'], east_book.provenance[0]['unit']), (3000, 'mL'))
        self.assertEqual(
            [(ref['reading_id'], ref['raw'], ref['litres'], ref['missing']) for ref in west_book.provenance],
            [('west-missing', None, None, True), ('west-zero', 0, 0, False)],
        )
        for source, altered in (
            ('another-report', rows),
            ('first-report', [{**east, 'value': 3, 'unit': 'L'}, missing, zero]),
            ('first-report', [east, {**missing, 'value': 0}, zero]),
            ('first-report', [east, missing, {**zero, 'reading_id': 'different-zero'}]),
        ):
            with self.subTest(source=source, altered=altered):
                with self.assertRaises(IngestError):
                    ingest(db, 'opening', source, altered)
        self.assertEqual(read(db, day=DAY), view)

    def test_nonintegral_litre(self):
        db = self.warehouse('fractional')
        valid = self.observations['rows'][0]
        fractional = {**valid, 'reading_id': 'fractional', 'value': 500}
        with self.assertRaises(IngestError):
            ingest(db, 'mixed', 'first-report', [valid, fractional])
        self.assertIsNone(find_batch(db, 'mixed'))
        self.assertIsNone(read(db, day=DAY))
        ingest(db, 'mixed', 'first-report', [valid])
        self.assertEqual(read(db, day=DAY).total_litres, 3)

    def test_station_identity(self):
        db = self.warehouse('registry')
        original = register(db, 'P-E', EAST, ['mL', 'L'])
        self.assertEqual(original, register(db, 'P-E', EAST, ['L', 'mL']))
        with self.assertRaises(RegistrationError):
            register(db, 'P-E', WEST, ['L', 'mL'])
        self.assertEqual(original, find_station(db, 'P-E'))
