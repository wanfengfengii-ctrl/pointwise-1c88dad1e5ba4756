from __future__ import annotations

import sqlite3

from audit.capture import build_audit_view, save_audit_view
from daily.snapshots import take_day_snapshot
from intake.stations import require_identifier
from issue.package import FrozenPackage, decode_package
from receive.eligibility import CheckMismatch, EligibilityError, evaluate_package
from receive.posting import PostedAdjustment, append_posted_adjustments
from receive.receipts import Receipt, bind_receipt, find_receipt_by_acceptance
from receive.transaction import CommitFailure, posting_transaction


class AdmissionError(ValueError):
    pass


class IncompatiblePackage(AdmissionError):
    pass


class CommitError(AdmissionError):
    pass


def _publish(db: sqlite3.Connection, acceptance_id: str, package: FrozenPackage, identity: str) -> Receipt:
    snapshot = take_day_snapshot(db, package.day)
    plan = evaluate_package(snapshot, package)
    receipt = Receipt(acceptance_id, f'RC-{acceptance_id}', identity, package.source_batch, plan.day)
    bind_receipt(db, receipt)
    append_posted_adjustments(db, (
        PostedAdjustment(acceptance_id, identity, package.source_batch, plan.day, region, delta)
        for region, delta in plan.changes
    ))
    published = build_audit_view(take_day_snapshot(db, plan.day))
    save_audit_view(db, receipt.receipt_number, published)
    return receipt


def accept(db: sqlite3.Connection, acceptance_id: str, wire: bytes) -> Receipt:
    try:
        require_identifier(acceptance_id, 'acceptance_id')
        package = decode_package(wire)
        identity = package.identity
        with posting_transaction(db):
            existing = find_receipt_by_acceptance(db, acceptance_id)
            if existing is not None:
                if existing.package_id != identity:
                    raise AdmissionError(f'acceptance id {acceptance_id} already refers to another package')
                return existing
            return _publish(db, acceptance_id, package, identity)
    except AdmissionError:
        raise
    except CheckMismatch as error:
        raise IncompatiblePackage(str(error)) from error
    except CommitFailure as error:
        raise CommitError(str(error)) from error
    except (EligibilityError, ValueError, sqlite3.Error) as error:
        raise AdmissionError(str(error)) from error
