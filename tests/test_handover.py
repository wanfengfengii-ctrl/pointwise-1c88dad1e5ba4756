from audit.ledger import read
from daily.supplements import supplement
from receive.admission import IncompatiblePackage, accept
from receive.receipts import find_receipt_by_acceptance
from tests import DAY, EAST, WEST, WarehouseCase


class TestHandover(WarehouseCase):
    def test_compatible_local_supplement(self):
        wire = self.issue()
        local = self.warehouse('compatible', seed=True)
        supplement(local, 'independent-two', DAY, EAST, 2)
        receipt = accept(local, 'night-review', wire)
        live = read(local, day=DAY)
        self.assertEqual(live.total_litres, 15)
        self.assertEqual(read(local, receipt_number=receipt.receipt_number), live)
        east, west = self.regions(local)[EAST], self.regions(local)[WEST]
        self.assertEqual(east.litres, 15)
        self.assertEqual((west.litres, west.missing_reads, west.valid_reads), (0, 1, 1))
        self.assertEqual([ref['reading_id'] for ref in west.provenance], ['west-missing', 'west-zero'])
        self.assertEqual(east.provenance[-2]['supplement_id'], 'independent-two')
        self.assertEqual(east.provenance[-2]['delta'], 2)
        self.assertEqual(east.provenance[-1]['package_id'], receipt.package_id)
        self.assertEqual(east.provenance[-1]['source_batch'], self.correction['batch_id'])
        self.assertEqual(east.provenance[-1]['delta'], 3)

    def test_incompatible_check(self):
        wire = self.issue()
        local = self.warehouse('incompatible', seed=True)
        supplement(local, 'independent-seven', DAY, EAST, 7)
        before = read(local, day=DAY)
        self.assertEqual(before.total_litres, 17)
        with self.assertRaises(IncompatiblePackage):
            accept(local, 'night-review', wire)
        self.assertEqual(read(local, day=DAY), before)
        self.assertIsNone(find_receipt_by_acceptance(local, 'night-review'))
        self.assertIsNone(read(local, receipt_number='RC-night-review'))
