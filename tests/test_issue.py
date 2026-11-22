from audit.ledger import read
from daily.supplements import supplement
from issue.freeze import FreezeError, freeze
from issue.package import decode_package, encode_package
from issue.work_batch import CorrectionError, correct, find_work_batch
from receive.admission import IncompatiblePackage, accept
from tests import DAY, EAST, WarehouseCase, add, check


class TestIssue(WarehouseCase):
    def test_discarded_trial(self):
        source = self.warehouse('trial-source', seed=True)
        staged = correct(source, 'trial', DAY, [{'op': 'begin_trial'}, add(7), check('trial-17', 17)])
        self.assertEqual(staged.values[EAST], 10)
        self.assertEqual(staged.trial.values[EAST], 17)
        self.assertIs(staged.trial.events[-1]['matched'], True)
        batch = correct(source, 'trial', DAY, [{'op': 'withdraw_trial'}, add(3), check('formal-20', 20)])
        self.assertEqual(batch.values[EAST], 13)
        self.assertIsNone(batch.trial)
        self.assertEqual([row['name'] for row in batch.formal_events if row['op'] == 'check'], ['formal-20'])
        package = freeze(source, 'trial')
        wire = encode_package(package)
        self.assertNotIn(b'trial-17', wire)
        source.close()
        local = self.warehouse('trial-review', seed=True)
        supplement(local, 'own', DAY, EAST, 2)
        receipt = accept(local, 'trial-review', wire)
        self.assertEqual(read(local, receipt_number=receipt.receipt_number).total_litres, 15)

    def test_open_trial(self):
        source = self.warehouse('open-trial', seed=True)
        correct(source, 'open', DAY, [add(1), {'op': 'begin_trial'}, add(2), check('trial-target', 13)])
        with self.assertRaises(FreezeError):
            freeze(source, 'open')
        source.close()
        source = self.warehouse('open-trial')
        still_open = find_work_batch(source, 'open')
        self.assertEqual(still_open.values[EAST], 11)
        self.assertEqual(still_open.trial.values[EAST], 13)
        correct(source, 'open', DAY, [{'op': 'keep_trial'}])
        package = freeze(source, 'open')
        wire = encode_package(package)
        supplement(source, 'next-round', DAY, EAST, 5)
        self.assertEqual(decode_package(wire).identity, package.identity)
        self.assertEqual(encode_package(freeze(source, 'open')), wire)
        with self.assertRaises(CorrectionError):
            correct(source, 'open', DAY, [add(1)])
        source.close()
        local = self.warehouse('kept-trial', seed=True)
        self.assertEqual(accept(local, 'from-trial', wire).package_id, package.identity)
        self.assertEqual(read(local, day=DAY).total_litres, 13)

    def test_positioned_evidence(self):
        early = self.issue([check('boundary', 20), add(3)], file='early', batch_id='same-batch')
        late = self.issue([add(3), check('boundary', 20)], file='late', batch_id='same-batch')
        self.assertNotEqual(decode_package(early).identity, decode_package(late).identity)
        for wire, claim in ((early, 'early'), (late, 'late')):
            local = self.warehouse(claim + '-review', seed=True)
            supplement(local, 'own', DAY, EAST, 7)
            if claim == 'early':
                receipt = accept(local, claim, wire)
                self.assertEqual(read(local, receipt_number=receipt.receipt_number).total_litres, 20)
            else:
                with self.assertRaises(IncompatiblePackage):
                    accept(local, claim, wire)
                self.assertEqual(read(local, day=DAY).total_litres, 17)
                self.assertIsNone(read(local, receipt_number='RC-late'))

    def test_intermediate_truth(self):
        operations = [add(3), check('middle-13', 13), add(-2)]
        source = self.warehouse('intermediate', seed=True)
        batch = correct(source, 'middle', DAY, operations)
        self.assertEqual(batch.values[EAST], 11)
        self.assertIs(batch.formal_events[1]['matched'], True)
        wire = encode_package(freeze(source, 'middle'))
        source.close()
        local = self.warehouse('middle-review', seed=True)
        receipt = accept(local, 'middle-review', wire)
        self.assertEqual(read(local, day=DAY).total_litres, 11)
        self.assertEqual(read(local, receipt_number=receipt.receipt_number).total_litres, 11)
        extra = self.warehouse('middle-rejected', seed=True)
        supplement(extra, 'own', DAY, EAST, 2)
        with self.assertRaises(IncompatiblePackage):
            accept(extra, 'middle-review', wire)
        self.assertEqual(read(extra, day=DAY).total_litres, 12)
