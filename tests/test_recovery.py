import sqlite3

from audit.ledger import read
from daily.supplements import supplement
from receive.admission import AdmissionError, CommitError, accept
from receive.posting import read_posted_adjustments
from receive.receipts import find_receipt_by_acceptance
from tests import DAY, EAST, WEST, WarehouseCase, add, check


class TestRecovery(WarehouseCase):
    def test_commit_retry(self):
        wire = self.issue([add(4, WEST), add(3), check('limit-20', 20)])
        local = self.warehouse('retry', seed=True)
        supplement(local, 'independent-two', DAY, EAST, 2)
        before = read(local, day=DAY)
        local.execute('CREATE TABLE test_commit_parent (id INTEGER PRIMARY KEY)')
        local.execute('''
            CREATE TABLE test_commit_attempt (
                parent_id INTEGER REFERENCES test_commit_parent(id) DEFERRABLE INITIALLY DEFERRED
            )
        ''')
        local.execute('''
            CREATE TRIGGER test_commit_gate AFTER INSERT ON audit_views
            BEGIN
                INSERT INTO test_commit_attempt VALUES (1);
            END
        ''')
        with self.assertRaises(CommitError) as failed:
            accept(local, 'retry', wire)
        sqlite_error = failed.exception.__cause__.__cause__
        self.assertIsInstance(sqlite_error, sqlite3.IntegrityError)
        self.assertIn('FOREIGN KEY', str(sqlite_error))
        self.assertFalse(local.in_transaction)
        local.close()

        reopened = self.warehouse('retry')
        self.assertEqual(read(reopened, day=DAY), before)
        self.assertEqual(read_posted_adjustments(reopened, DAY), ())
        self.assertIsNone(find_receipt_by_acceptance(reopened, 'retry'))
        self.assertIsNone(read(reopened, receipt_number='RC-retry'))
        self.assertEqual(reopened.execute('SELECT COUNT(*) FROM audit_views').fetchone()[0], 0)
        self.assertEqual(reopened.execute('SELECT COUNT(*) FROM test_commit_attempt').fetchone()[0], 0)
        reopened.execute('DROP TRIGGER test_commit_gate')
        receipt = accept(reopened, 'retry', wire)
        published = read(reopened, receipt_number=receipt.receipt_number)
        self.assertEqual([item.litres for item in published.regions], [15, 4])
        self.assertEqual(published.total_litres, 19)
        reopened.close()

        final = self.warehouse('retry')
        self.assertEqual(accept(final, 'retry', wire), receipt)
        self.assertEqual(read(final, day=DAY), published)
        self.assertEqual(read(final, receipt_number=receipt.receipt_number), published)
        self.assertEqual(len(read_posted_adjustments(final, DAY)), 2)

    def test_different_package_under_posted_id(self):
        wire = self.issue()
        different = self.issue([add(5)], file='alternative-source', batch_id='alternative')
        local = self.warehouse('conflict', seed=True)
        supplement(local, 'independent-two', DAY, EAST, 2)
        receipt = accept(local, 'posted', wire)
        audit = read(local, receipt_number=receipt.receipt_number)
        supplement(local, 'next-shift', DAY, EAST, 2)
        local.close()

        reopened = self.warehouse('conflict')
        with self.assertRaises(AdmissionError):
            accept(reopened, 'posted', different)
        self.assertEqual(accept(reopened, 'posted', wire), receipt)
        live = read(reopened, day=DAY)
        self.assertEqual(live.total_litres, 17)
        self.assertEqual(live.regions[0].provenance[-2]['supplement_id'], 'next-shift')
        stable = read(reopened, receipt_number=receipt.receipt_number)
        self.assertEqual(stable, audit)
        self.assertEqual(stable.total_litres, 15)
        self.assertEqual([region.litres for region in stable.regions], [15, 0])
        self.assertEqual(stable.regions[1].missing_reads, 1)
        self.assertNotIn('next-shift', [ref.get('supplement_id') for ref in stable.regions[0].provenance])
