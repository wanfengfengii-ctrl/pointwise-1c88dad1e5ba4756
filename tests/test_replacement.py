import json
import sqlite3

from audit.ledger import read
from daily.supplements import supplement
from daily.warehouse import initialize_warehouse, open_warehouse
from intake.batches import ingest
from issue.freeze import FreezeError, freeze, freeze_replacement
from issue.package import decode_package, decode_replacement, encode_package, encode_replacement
from issue.work_batch import correct
from receive.admission import (
    AdmissionError,
    CommitError,
    IncompatiblePackage,
    accept,
    accept_replacement,
)
from receive.receipts import find_receipt_by_acceptance
from tests import DAY, EAST, WarehouseCase, add, check, load_sample


class ReplacementCase(WarehouseCase):
    """Build a source warehouse and a separate reviewer warehouse from samples."""

    def source(self, name='source'):
        db = open_warehouse(self.root / f'{name}.sqlite')
        self.addCleanup(db.close)
        initialize_warehouse(db, self.stations)
        self.seed_day(db)
        return db

    def reviewer(self, name='reviewer', *, seed=True):
        db = open_warehouse(self.root / f'{name}.sqlite')
        self.addCleanup(db.close)
        initialize_warehouse(db, self.stations)
        if seed:
            self.seed_day(db)
        return db

    def ordinary_wire(self, db, batch_id, operations):
        correct(db, batch_id, DAY, operations)
        return encode_package(freeze(db, batch_id))

    def replacement_wire(self, db, original, batch_id, operations):
        correct(db, batch_id, DAY, operations)
        return freeze_replacement(db, original, batch_id)


class TestSampleJoint(ReplacementCase):
    def test_sample_handoff_and_in_place_corrections(self):
        scenario = load_sample('replacement.json')
        region = scenario['region']
        source = self.source()
        wires, replacements = {}, {}
        for step in scenario['source_steps']:
            if 'supplement_id' in step:
                supplement(source, step['supplement_id'], DAY, region, step['litres'])
                continue
            batch_id = step['batch_id']
            correct(source, batch_id, DAY, step['operations'])
            if 'replaces' in step:
                replacements[batch_id] = freeze_replacement(source, step['replaces'], batch_id)
            else:
                wires[batch_id] = encode_package(freeze(source, batch_id))
        source.close()

        reviewer = self.reviewer()
        for step in scenario['review_prefix']:
            if 'supplement_id' in step:
                supplement(reviewer, step['supplement_id'], DAY, region, step['litres'])
            else:
                accept(reviewer, step['acceptance_id'], wires[step['batch_id']])
            self.assertEqual(read(reviewer, day=DAY).total_litres, step['total_litres'])

        before = read(reviewer, day=DAY)
        self.assertEqual(before.total_litres, 24)
        with self.assertRaises(IncompatiblePackage):
            accept_replacement(reviewer, 'a4', 'RC-z-first', replacements['A4'])
        self.assertEqual(read(reviewer, day=DAY), before)
        self.assertIsNone(find_receipt_by_acceptance(reviewer, 'a4'))

        a5 = accept_replacement(reviewer, 'a5', 'RC-z-first', replacements['A5'])
        self.assertEqual(a5.receipt_number, 'RC-a5')
        self.assertEqual(a5.source_batch, 'A5')
        self.assertEqual(read(reviewer, day=DAY).total_litres, 26)

        accept(reviewer, 'm-third', wires['C'])
        self.assertEqual(read(reviewer, day=DAY).total_litres, 26)

        b2 = accept_replacement(reviewer, 'b2', 'RC-a-second', replacements['B2'])
        live = read(reviewer, day=DAY)
        self.assertEqual(live.total_litres, 26)
        self.assertEqual(read(reviewer, receipt_number=b2.receipt_number), live)

        self.assertEqual(read(reviewer, receipt_number='RC-z-first').total_litres, 15)
        self.assertEqual(read(reviewer, receipt_number='RC-a-second').total_litres, 20)

        # Re-claiming the same acceptance id, target and wire is stable.
        self.assertEqual(accept_replacement(reviewer, 'b2', 'RC-a-second', replacements['B2']), b2)
        self.assertEqual(read(reviewer, day=DAY), live)
        # A different acceptance id cannot replace the same receipt again.
        with self.assertRaises(AdmissionError):
            accept_replacement(reviewer, 'b2-again', 'RC-a-second', replacements['B2'])
        self.assertEqual(read(reviewer, day=DAY), live)
        # The replaced receipt is no longer an eligible target.
        with self.assertRaises(AdmissionError):
            accept_replacement(reviewer, 'b2-third', 'RC-a5', replacements['B2'])


class TestInPlaceReplay(ReplacementCase):
    def test_downstream_reverified_at_own_position(self):
        source = self.source()
        supplement(source, 'mirror', DAY, EAST, 3)  # reviewer is at 13 L once A is accepted
        a = self.ordinary_wire(source, 'A', [add(3)])
        c = self.ordinary_wire(source, 'C', [add(2), check('c15', 15)])
        a5 = self.replacement_wire(source, 'A', 'A5', [add(5)])
        source.close()

        reviewer = self.reviewer()
        accept(reviewer, 'a', a)
        c_receipt = accept(reviewer, 'c', c)
        self.assertEqual(read(reviewer, day=DAY).total_litres, 15)
        frozen_c = read(reviewer, receipt_number=c_receipt.receipt_number)

        # A5 lifts the shared balance by 2 L, so C would now sit at 17 L and its check falsifies.
        before = read(reviewer, day=DAY)
        with self.assertRaises(IncompatiblePackage):
            accept_replacement(reviewer, 'a5', 'RC-a', a5)
        self.assertEqual(read(reviewer, day=DAY), before)
        self.assertEqual(read(reviewer, receipt_number=c_receipt.receipt_number), frozen_c)

    def test_historical_view_of_replaced_receipt_stays_fixed(self):
        source = self.source()
        a = self.ordinary_wire(source, 'A', [add(3)])
        a5 = self.replacement_wire(source, 'A', 'A5', [add(5)])
        source.close()

        reviewer = self.reviewer()
        receipt = accept(reviewer, 'a', a)
        frozen = read(reviewer, receipt_number=receipt.receipt_number)
        supplement(reviewer, 'later', DAY, EAST, 4)
        accept_replacement(reviewer, 'a5', 'RC-a', a5)
        self.assertEqual(read(reviewer, receipt_number=receipt.receipt_number), frozen)
        self.assertEqual(read(reviewer, receipt_number=receipt.receipt_number).total_litres, 13)

    def test_zero_net_replacement_succeeds(self):
        source = self.source()
        a = self.ordinary_wire(source, 'A', [add(3)])
        zero = self.replacement_wire(source, 'A', 'Z',
                                     [add(3), check('z13', 13), add(-3)])
        source.close()
        reviewer = self.reviewer()
        accept(reviewer, 'a', a)
        receipt = accept_replacement(reviewer, 'z', 'RC-a', zero)
        audit = read(reviewer, receipt_number=receipt.receipt_number)
        self.assertEqual(audit.total_litres, 10)
        self.assertEqual(audit.regions[0].provenance[-1]['delta'], 0)


class TestIndependentReplay(ReplacementCase):
    def test_replace_only_second_of_two_ordinary_acceptances(self):
        source = self.source()
        a = self.ordinary_wire(source, 'A', [add(3)])
        a5 = self.replacement_wire(source, 'A', 'A5', [add(5)])
        source.close()

        reviewer = self.reviewer()
        first = accept(reviewer, 'first-a', a)
        second = accept(reviewer, 'second-a', a)
        self.assertEqual(read(reviewer, day=DAY).total_litres, 16)

        replaced = accept_replacement(reviewer, 'second-a-fix', 'RC-second-a', a5)
        self.assertEqual(read(reviewer, day=DAY).total_litres, 18)  # only +2 L at that slot
        self.assertEqual(read(reviewer, receipt_number=first.receipt_number).total_litres, 13)
        self.assertEqual(read(reviewer, receipt_number=second.receipt_number).total_litres, 16)
        self.assertEqual(read(reviewer, receipt_number=replaced.receipt_number).total_litres, 18)

        # Same target under a fresh id is refused; the first receipt is still replaceable.
        with self.assertRaises(AdmissionError):
            accept_replacement(reviewer, 'second-a-fix-2', 'RC-second-a', a5)
        accept_replacement(reviewer, 'first-a-fix', 'RC-first-a', a5)
        self.assertEqual(read(reviewer, day=DAY).total_litres, 20)

    def test_downstream_check_flip_is_rejected(self):
        source = self.source()
        # Author Q against a 13 L base (observations 10 + a source-side mirror supplement),
        # matching what the reviewer holds once the ordinary A is accepted.
        supplement(source, 'mirror', DAY, EAST, 3)
        a = self.ordinary_wire(source, 'A', [add(3)])
        q = self.ordinary_wire(source, 'Q', [add(2), check('q15', 15)])
        a7 = self.replacement_wire(source, 'A', 'A7', [add(7)])
        source.close()

        reviewer = self.reviewer()
        accept(reviewer, 'a', a)
        accept(reviewer, 'q', q)
        self.assertEqual(read(reviewer, day=DAY).total_litres, 15)
        before = read(reviewer, day=DAY)

        # Replacing A with +7 L shifts the downstream Q check from true to false.
        with self.assertRaises(IncompatiblePackage):
            accept_replacement(reviewer, 'a7', 'RC-a', a7)
        self.assertEqual(read(reviewer, day=DAY), before)
        self.assertIsNone(find_receipt_by_acceptance(reviewer, 'a7'))
        self.assertIsNone(read(reviewer, receipt_number='RC-a7'))

    def test_replacement_own_check_flip_is_rejected(self):
        source = self.source()
        a = self.ordinary_wire(source, 'A', [add(3)])
        # Built on the source base (10 L): 10 + 6 == 16 is true there.
        shifted = self.replacement_wire(source, 'A', 'R', [add(6), check('r16', 16)])
        source.close()

        reviewer = self.reviewer()
        supplement(reviewer, 'open', DAY, EAST, 3)  # local base is 13 L at the target slot
        accept(reviewer, 'a', a)
        before = read(reviewer, day=DAY)
        with self.assertRaises(IncompatiblePackage):
            accept_replacement(reviewer, 'r', 'RC-a', shifted)
        self.assertEqual(read(reviewer, day=DAY), before)

    def test_later_replacement_is_honoured_when_replaying(self):
        source = self.source()
        supplement(source, 'mirror', DAY, EAST, 3)
        a = self.ordinary_wire(source, 'A', [add(3)])
        b = self.ordinary_wire(source, 'B', [check('b13', 13), add(2)])
        b2 = self.replacement_wire(source, 'B', 'B2', [add(1), check('b2-16', 16), add(1)])
        source.close()

        reviewer = self.reviewer()
        accept(reviewer, 'a', a)
        accept(reviewer, 'b', b)
        self.assertEqual(read(reviewer, day=DAY).total_litres, 15)

        # B2 sits at B's slot on the reviewer's 13 L base and its own check is true there.
        accept_replacement(reviewer, 'b2', 'RC-b', b2)
        self.assertEqual(read(reviewer, day=DAY).total_litres, 15)
        self.assertEqual(read(reviewer, receipt_number='RC-b').total_litres, 15)


class TestModesAndOccupancy(ReplacementCase):
    def test_ordinary_accept_refuses_replacement_bytes(self):
        source = self.source()
        a = self.ordinary_wire(source, 'A', [add(3)])
        a5 = self.replacement_wire(source, 'A', 'A5', [add(5)])
        source.close()
        reviewer = self.reviewer()
        accept(reviewer, 'a', a)
        before = read(reviewer, day=DAY)
        with self.assertRaises(AdmissionError):
            accept(reviewer, 'a5-ordinary', a5)
        self.assertEqual(read(reviewer, day=DAY), before)
        self.assertIsNone(find_receipt_by_acceptance(reviewer, 'a5-ordinary'))

    def test_replacement_accept_refuses_ordinary_bytes(self):
        source = self.source()
        a = self.ordinary_wire(source, 'A', [add(3)])
        a5 = self.ordinary_wire(source, 'A5', [add(5)])
        source.close()
        reviewer = self.reviewer()
        accept(reviewer, 'a', a)
        before = read(reviewer, day=DAY)
        with self.assertRaises(AdmissionError):
            accept_replacement(reviewer, 'a5', 'RC-a', a5)
        self.assertEqual(read(reviewer, day=DAY), before)

    def test_acceptance_id_is_shared_across_entries(self):
        source = self.source()
        a = self.ordinary_wire(source, 'A', [add(3)])
        a5 = self.replacement_wire(source, 'A', 'A5', [add(5)])
        c = self.ordinary_wire(source, 'C', [check('c13', 13)])
        source.close()

        reviewer = self.reviewer()
        accept(reviewer, 'shared', a)
        with self.assertRaises(AdmissionError):
            accept_replacement(reviewer, 'shared', 'RC-shared', a5)
        with self.assertRaises(AdmissionError):
            accept(reviewer, 'shared', c)

        other = self.reviewer('other')
        accept(other, 'first', a)
        accept_replacement(other, 'shared', 'RC-first', a5)
        with self.assertRaises(AdmissionError):
            accept(other, 'shared', c)

    def test_rebound_target_under_existing_id_is_refused(self):
        source = self.source()
        a = self.ordinary_wire(source, 'A', [add(3)])
        a5 = self.replacement_wire(source, 'A', 'A5', [add(5)])
        b = self.ordinary_wire(source, 'B', [add(2)])
        source.close()

        reviewer = self.reviewer()
        accept(reviewer, 'a', a)
        accept(reviewer, 'b', b)
        accept_replacement(reviewer, 'fix', 'RC-a', a5)
        live = read(reviewer, day=DAY)
        with self.assertRaises(AdmissionError):
            accept_replacement(reviewer, 'fix', 'RC-b', a5)
        self.assertEqual(read(reviewer, day=DAY), live)


class TestLegacyAndRecovery(ReplacementCase):
    def test_day_without_review_basis_accepts_but_cannot_be_replaced(self):
        source = self.source()
        a = self.ordinary_wire(source, 'A', [add(3)])
        a5 = self.replacement_wire(source, 'A', 'A5', [add(5)])
        one = self.ordinary_wire(source, 'one', [add(1)])
        source.close()

        reviewer = self.reviewer()
        accept(reviewer, 'a', a)
        # Simulate a day recorded before the upgrade: the historical posting basis is absent.
        reviewer.execute("DELETE FROM program_log WHERE kind = 'observation_batch'")
        reviewer.commit()

        supplement(reviewer, 'late', DAY, EAST, 2)
        accept(reviewer, 'after', one)
        self.assertEqual(read(reviewer, day=DAY).total_litres, 16)
        before = read(reviewer, day=DAY)
        with self.assertRaises(AdmissionError):
            accept_replacement(reviewer, 'a5', 'RC-a', a5)
        self.assertEqual(read(reviewer, day=DAY), before)
        self.assertIsNone(find_receipt_by_acceptance(reviewer, 'a5'))

    def test_commit_failure_leaves_no_result_and_can_be_retried(self):
        source = self.source()
        a = self.ordinary_wire(source, 'A', [add(3)])
        a5 = self.replacement_wire(source, 'A', 'A5', [add(5)])
        source.close()

        reviewer = self.reviewer()
        accept(reviewer, 'a', a)
        before = read(reviewer, day=DAY)
        reviewer.execute('CREATE TABLE gate_parent (id INTEGER PRIMARY KEY)')
        reviewer.execute('''
            CREATE TABLE gate_child (pid INTEGER REFERENCES gate_parent(id)
                                     DEFERRABLE INITIALLY DEFERRED)
        ''')
        reviewer.execute('''
            CREATE TRIGGER gate AFTER INSERT ON audit_views
            BEGIN INSERT INTO gate_child VALUES (1); END
        ''')
        with self.assertRaises(CommitError) as failed:
            accept_replacement(reviewer, 'a5', 'RC-a', a5)
        self.assertIsInstance(failed.exception.__cause__.__cause__, sqlite3.IntegrityError)
        self.assertFalse(reviewer.in_transaction)
        self.assertEqual(read(reviewer, day=DAY), before)
        self.assertIsNone(find_receipt_by_acceptance(reviewer, 'a5'))
        self.assertEqual(reviewer.execute('SELECT COUNT(*) FROM replacement_links').fetchone()[0], 0)
        reviewer.close()

        reopened = self.reviewer('reviewer')
        self.assertEqual(read(reopened, day=DAY), before)
        reopened.execute('DROP TRIGGER gate')
        receipt = accept_replacement(reopened, 'a5', 'RC-a', a5)
        self.assertEqual(read(reopened, day=DAY).total_litres, 15)
        reopened.close()

        final = self.reviewer('reviewer')
        self.assertEqual(accept_replacement(final, 'a5', 'RC-a', a5), receipt)


class TestIssuance(ReplacementCase):
    def test_freeze_replacement_guards(self):
        source = self.source()
        source.close()
        source = self.source()
        correct(source, 'A', DAY, [add(3)])
        freeze(source, 'A')
        correct(source, 'A5', DAY, [add(5)])
        correct(source, 'A6', DAY, [add(6)])
        correct(source, 'open', DAY, [{'op': 'begin_trial'}, add(1)])

        with self.assertRaises(FreezeError):
            freeze_replacement(source, 'absent', 'A5')
        with self.assertRaises(FreezeError):
            freeze_replacement(source, 'A', 'A')
        with self.assertRaises(FreezeError):
            freeze_replacement(source, 'A', 'open')
        with self.assertRaises(FreezeError):
            freeze_replacement(source, 'A5', 'A6')  # A5 is not an ordinary original yet

        first = freeze_replacement(source, 'A', 'A5')
        self.assertEqual(freeze_replacement(source, 'A', 'A5'), first)
        self.assertEqual(decode_replacement(first).replacement.source_batch, 'A5')
        # One ordinary package may back several distinct replacements.
        freeze_replacement(source, 'A', 'A6')
        with self.assertRaises(FreezeError):
            freeze_replacement(source, 'A6', 'A5')
        with self.assertRaises(FreezeError):
            freeze(source, 'A5')

    def test_source_reopen_reissues_identical_bytes(self):
        source = self.source()
        correct(source, 'A', DAY, [add(3)])
        freeze(source, 'A')
        correct(source, 'A5', DAY, [add(5)])
        first = freeze_replacement(source, 'A', 'A5')
        source.close()

        reopened = self.source()
        self.assertEqual(freeze_replacement(reopened, 'A', 'A5'), first)

    def test_replacement_must_share_the_original_day(self):
        source = self.source()
        correct(source, 'A', DAY, [add(3)])
        freeze(source, 'A')
        other_day = '2026-11-17'
        rows = [dict(row, day=other_day, reading_id=f'next-{row["reading_id"]}')
                for row in self.observations['rows']]
        ingest(source, 'raw-next', '次日原日报', rows)
        correct(source, 'other', other_day, [add(1)])
        with self.assertRaises(FreezeError):
            freeze_replacement(source, 'A', 'other')

    def test_decode_rejects_tampered_replacement(self):
        source = self.source()
        correct(source, 'A', DAY, [add(3)])
        freeze(source, 'A')
        correct(source, 'A5', DAY, [add(5)])
        wire = freeze_replacement(source, 'A', 'A5')
        envelope = json.loads(wire)
        envelope['body']['replacement']['body']['events'][0]['after'] += 1
        tampered = json.dumps(envelope).encode('utf-8')
        with self.assertRaises(ValueError):
            decode_replacement(tampered)
