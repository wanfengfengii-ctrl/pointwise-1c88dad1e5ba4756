import json

from audit.ledger import read
from daily.supplements import supplement
from intake.batches import IngestError, find_batch, ingest
from issue.work_batch import CorrectionError, correct, find_work_batch
from receive.admission import AdmissionError, IncompatiblePackage, accept
from receive.receipts import find_receipt_by_acceptance
from tests import DAY, EAST, WEST, WarehouseCase, add, check


class TestExpanded(WarehouseCase):
    def test_zero_net_still_checks_local_balance(self):
        wire = self.issue([add(3), check('crosses-20', 20), add(-3)])
        compatible = self.warehouse('zero-compatible', seed=True)
        supplement(compatible, 'local-two', DAY, EAST, 2)
        receipt = accept(compatible, 'zero', wire)
        audit = read(compatible, receipt_number=receipt.receipt_number)
        self.assertEqual(audit.total_litres, 12)
        self.assertEqual(audit.regions[0].provenance[-1]['delta'], 0)
        self.assertEqual(audit.regions[0].provenance[-1]['package_id'], receipt.package_id)

        incompatible = self.warehouse('zero-incompatible', seed=True)
        supplement(incompatible, 'local-seven', DAY, EAST, 7)
        before = read(incompatible, day=DAY)
        with self.assertRaises(IncompatiblePackage):
            accept(incompatible, 'zero', wire)
        self.assertEqual(read(incompatible, day=DAY), before)
        self.assertIsNone(read(incompatible, receipt_number='RC-zero'))

    def test_unregistered_check_only_region(self):
        wire = self.issue([add(3), check('west-zero', 0, WEST)])
        local = self.warehouse('east-only', stations=self.stations[:1])
        supplement(local, 'opening', DAY, EAST, 10)
        before = read(local, day=DAY)
        with self.assertRaisesRegex(AdmissionError, 'unregistered day region'):
            accept(local, 'unregistered', wire)
        self.assertEqual(read(local, day=DAY), before)
        self.assertIsNone(find_receipt_by_acceptance(local, 'unregistered'))

    def test_late_mismatch_leaves_all_regions_unchanged(self):
        wire = self.issue([add(4, WEST), add(3), check('east-limit', 20)])
        local = self.warehouse('all-or-none', seed=True)
        supplement(local, 'local-seven', DAY, EAST, 7)
        before = read(local, day=DAY)
        with self.assertRaises(IncompatiblePackage):
            accept(local, 'whole', wire)
        self.assertEqual(read(local, day=DAY), before)
        self.assertEqual([region.litres for region in before.regions], [17, 0])
        self.assertIsNone(read(local, receipt_number='RC-whole'))

    def test_invalid_exchange_does_not_create_or_reuse_success(self):
        wire = self.issue([add(3)])
        envelope = json.loads(wire)
        envelope['body']['day'] = '2026-11-17'
        altered = json.dumps(envelope).encode('utf-8')
        local = self.warehouse('invalid-wire', seed=True)
        before = read(local, day=DAY)
        with self.assertRaises(AdmissionError):
            accept(local, 'wire-check', altered)
        self.assertEqual(read(local, day=DAY), before)
        self.assertIsNone(find_receipt_by_acceptance(local, 'wire-check'))
        receipt = accept(local, 'wire-check', wire)
        with self.assertRaises(AdmissionError):
            accept(local, 'wire-check', altered)
        self.assertEqual(read(local, receipt_number=receipt.receipt_number).total_litres, 13)

    def test_invalid_operations_leave_work_batch_unchanged(self):
        source = self.warehouse('work-rollback', seed=True)
        original = correct(source, 'work', DAY, [add(1), check('unique', 11)])
        with self.assertRaises(CorrectionError):
            correct(source, 'work', DAY, [add(3), check('unique', 14)])
        self.assertEqual(find_work_batch(source, 'work'), original)
        with self.assertRaises(CorrectionError):
            correct(source, 'work', DAY, [add(1, '北区')])
        self.assertEqual(find_work_batch(source, 'work'), original)

    def test_unknown_station_and_disallowed_missing_unit_are_batch_errors(self):
        local = self.warehouse('bad-inputs')
        east, _, missing, _ = self.observations['rows']
        for bad in ({**missing, 'station_id': 'absent'}, {**missing, 'unit': 'mL'}):
            with self.subTest(row=bad):
                with self.assertRaises(IngestError):
                    ingest(local, 'bad', 'report', [east, bad])
                self.assertIsNone(find_batch(local, 'bad'))
                self.assertIsNone(read(local, day=DAY))

    def test_quantities_remain_exact_beyond_sqlite_integer_range(self):
        local = self.warehouse('large')
        amount = 10**25
        row = {**self.observations['rows'][0], 'unit': 'L', 'value': amount}
        ingest(local, 'large', 'report', [row])
        supplement(local, 'large-negative', DAY, EAST, -amount + 10)
        wire = self.issue([add(3), check('thirteen', 13)])
        receipt = accept(local, 'large', wire)
        historical = read(local, receipt_number=receipt.receipt_number)
        self.assertEqual(historical.total_litres, 13)
        self.assertEqual(historical.regions[0].provenance[0]['raw'], amount)
