from __future__ import annotations

import json
import sqlite3

from audit.capture import build_audit_view, save_audit_view
from daily.snapshots import take_day_snapshot
from intake.stations import require_identifier
from issue.package import (
    FrozenPackage,
    ReplacementPackage,
    decode_package,
    decode_replacement,
    encode_package,
    encode_replacement,
)
from receive.eligibility import CheckMismatch, EligibilityError, evaluate_package
from receive.posting import PostedAdjustment, append_posted_adjustments
from receive.program import (
    ProgramError,
    day_is_certified,
    encode_events,
    find_posting,
    find_replacement_link,
    record_posting,
    replay_day,
)
from receive.receipts import (
    Receipt,
    bind_receipt,
    find_receipt_by_acceptance,
    find_receipt_by_number,
)
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
    record_posting(db, acceptance_id, plan.day, package.events, encode_package(package))
    published = build_audit_view(take_day_snapshot(db, plan.day))
    save_audit_view(db, receipt.receipt_number, published)
    return receipt


def accept(db: sqlite3.Connection, acceptance_id: str, wire: bytes) -> Receipt:
    try:
        require_identifier(acceptance_id, 'acceptance_id')
        try:
            package = decode_package(wire)
        except ValueError:
            decode_replacement(wire)
            raise AdmissionError('ordinary acceptance refuses a replacement package')
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


def _replacement_target(db: sqlite3.Connection, target_receipt_number: str, package: ReplacementPackage) -> tuple[Receipt, int]:
    target = find_receipt_by_number(db, target_receipt_number)
    if target is None:
        raise AdmissionError(f'unknown target receipt {target_receipt_number}')
    if find_replacement_link(db, target.acceptance_id) is not None:
        raise AdmissionError(f'target receipt {target_receipt_number} is itself a replacement first posting')
    posting = find_posting(db, target.acceptance_id)
    if posting is None:
        raise AdmissionError(f'target receipt {target_receipt_number} is not an ordinary first posting')
    _, position, _, stored_wire, active = posting
    if not active:
        raise AdmissionError(f'target receipt {target_receipt_number} has already been replaced')
    if target.day != package.original.day:
        raise AdmissionError('target receipt belongs to another day than the replacement')
    if target.package_id != package.original.identity:
        raise AdmissionError('target receipt does not carry the original package named by the replacement')
    if decode_package(stored_wire).identity != package.original.identity:
        raise AdmissionError('target posting does not match the original package named by the replacement')
    if not day_is_certified(db, target.day):
        raise AdmissionError(f'day {target.day} lacks the review basis required for a replacement')
    return target, position


def _bind_replacement(
    db: sqlite3.Connection, acceptance_id: str, target: Receipt, package: ReplacementPackage, position: int,
) -> Receipt:
    from receive.program import find_posting_regions

    replacement = package.replacement
    receipt = Receipt(acceptance_id, f'RC-{acceptance_id}', package.identity,
                      replacement.source_batch, package.original.day)
    bind_receipt(db, receipt)
    scope = find_posting_regions(db, target.acceptance_id) or ()
    db.execute('UPDATE postings SET active = 0, superseded_by = ? WHERE acceptance_id = ?',
               (package.identity, target.acceptance_id))
    db.execute('''
        INSERT INTO postings (acceptance_id, day, position, events_json, regions_json, wire, active, superseded_by)
        VALUES (?, ?, ?, ?, ?, ?, 1, NULL)
    ''', (acceptance_id, package.original.day, position, encode_events(replacement.events),
          json.dumps(list(scope)), encode_replacement(package)))
    db.execute('''
        INSERT INTO replacement_links
            (acceptance_id, target_receipt_number, original_identity, replacement_identity)
        VALUES (?, ?, ?, ?)
    ''', (acceptance_id, target.receipt_number, package.original_identity, package.identity))
    db.execute('DELETE FROM posted_adjustments WHERE acceptance_id = ?', (target.acceptance_id,))
    return receipt


def accept_replacement(
    db: sqlite3.Connection, acceptance_id: str, target_receipt_number: str, wire: bytes,
) -> Receipt:
    try:
        require_identifier(acceptance_id, 'acceptance_id')
        require_identifier(target_receipt_number, 'target_receipt_number')
        package = decode_replacement(wire)
        with posting_transaction(db):
            existing = find_receipt_by_acceptance(db, acceptance_id)
            if existing is not None:
                link = find_replacement_link(db, acceptance_id)
                if (existing.package_id != package.identity or link is None
                        or link[0] != target_receipt_number):
                    raise AdmissionError(f'acceptance id {acceptance_id} already refers to another replacement')
                return existing
            target, position = _replacement_target(db, target_receipt_number, package)
            outcome = replay_day(db, package.original.day, substitute_position=position,
                                 substitute_events=package.replacement.events)
            receipt = _bind_replacement(db, acceptance_id, target, package, position)
            append_posted_adjustments(db, (
                PostedAdjustment(acceptance_id, package.identity, package.replacement.source_batch,
                                 package.original.day, region, delta)
                for region, delta in sorted(outcome.deltas[position].items())
            ))
            published = build_audit_view(take_day_snapshot(db, package.original.day))
            save_audit_view(db, receipt.receipt_number, published)
            return receipt
    except AdmissionError:
        raise
    except CheckMismatch as error:
        raise IncompatiblePackage(str(error)) from error
    except CommitFailure as error:
        raise CommitError(str(error)) from error
    except (EligibilityError, ProgramError, ValueError, sqlite3.Error) as error:
        raise AdmissionError(str(error)) from error
