from __future__ import annotations

import sqlite3

from intake.stations import require_identifier
from issue.package import (
    FrozenPackage,
    ReplacementPackage,
    decode_event,
    decode_package,
    encode_package,
    encode_replacement,
    package_identity,
)
from issue.work_batch import find_work_batch, seal_work_batch
from receive.transaction import write_transaction


class FreezeError(ValueError):
    pass


def initialize_frozen_packages(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS frozen_packages (
            batch_id TEXT PRIMARY KEY NOT NULL REFERENCES work_batches(batch_id),
            wire BLOB NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS replacement_packages (
            replacement_batch_id TEXT PRIMARY KEY NOT NULL REFERENCES work_batches(batch_id),
            original_batch_id TEXT NOT NULL REFERENCES work_batches(batch_id),
            original_identity TEXT NOT NULL,
            replacement_identity TEXT NOT NULL,
            wire BLOB NOT NULL
        )
    ''')


def find_frozen_package(db: sqlite3.Connection, batch_id: str) -> FrozenPackage | None:
    row = db.execute('SELECT wire FROM frozen_packages WHERE batch_id = ?', (batch_id,)).fetchone()
    return None if row is None else decode_package(row[0])


def freeze(db: sqlite3.Connection, batch_id: str) -> FrozenPackage:
    try:
        require_identifier(batch_id, 'batch_id')
        with write_transaction(db):
            existing = find_frozen_package(db, batch_id)
            if existing is not None:
                return existing
            if find_replacement_row(db, batch_id) is not None:
                raise FreezeError(f'work batch {batch_id} was already issued as a replacement')
            work = find_work_batch(db, batch_id)
            if work is None or work.trial is not None or work.sealed:
                raise FreezeError(f'work batch {batch_id} is missing, sealed without a package or still in trial')
            events = tuple(decode_event(row) for row in work.formal_events)
            package = FrozenPackage(work.batch_id, work.day, events)
            wire = encode_package(package)
            seal_work_batch(db, batch_id)
            db.execute('INSERT INTO frozen_packages (batch_id, wire) VALUES (?, ?)', (batch_id, wire))
            return package
    except FreezeError:
        raise
    except ValueError as error:
        raise FreezeError(str(error)) from error


def find_replacement_row(db: sqlite3.Connection, batch_id: str) -> tuple[str, str, str, bytes] | None:
    row = db.execute('''
        SELECT original_batch_id, original_identity, replacement_identity, wire
        FROM replacement_packages WHERE replacement_batch_id = ?
    ''', (batch_id,)).fetchone()
    return None if row is None else (row[0], row[1], row[2], bytes(row[3]))


def freeze_replacement(
    db: sqlite3.Connection, original_batch_id: str, replacement_batch_id: str,
) -> bytes:
    """Freeze a same-day work batch as an in-place replacement of an ordinary package."""
    try:
        require_identifier(original_batch_id, 'original_batch_id')
        require_identifier(replacement_batch_id, 'replacement_batch_id')
        with write_transaction(db):
            original = find_frozen_package(db, original_batch_id)
            if original is None:
                raise FreezeError(f'original batch {original_batch_id} has no ordinary frozen package')
            if find_replacement_row(db, original_batch_id) is not None:
                raise FreezeError(f'batch {original_batch_id} was issued as a replacement, not an ordinary package')
            if original_batch_id == replacement_batch_id:
                raise FreezeError('a work batch cannot replace itself')
            existing = find_replacement_row(db, replacement_batch_id)
            if existing is not None:
                stored_original, stored_identity, _, wire = existing
                if stored_original != original_batch_id or stored_identity != original.identity:
                    raise FreezeError(f'work batch {replacement_batch_id} is already bound to another original')
                return wire
            if find_frozen_package(db, replacement_batch_id) is not None:
                raise FreezeError(f'batch {replacement_batch_id} was issued as an ordinary package')
            work = find_work_batch(db, replacement_batch_id)
            if work is None or work.trial is not None or work.sealed:
                raise FreezeError(
                    f'replacement batch {replacement_batch_id} is missing, already sealed or still in trial'
                )
            if work.day != original.day:
                raise FreezeError('a replacement must belong to the same day as its original')
            events = tuple(decode_event(row) for row in work.formal_events)
            replacement = FrozenPackage(work.batch_id, work.day, events)
            package = ReplacementPackage(original, replacement)
            wire = encode_replacement(package)
            seal_work_batch(db, replacement_batch_id)
            db.execute('''
                INSERT INTO replacement_packages
                    (replacement_batch_id, original_batch_id, original_identity, replacement_identity, wire)
                VALUES (?, ?, ?, ?, ?)
            ''', (replacement_batch_id, original_batch_id, package.original_identity, package.identity, wire))
            return wire
    except FreezeError:
        raise
    except ValueError as error:
        raise FreezeError(str(error)) from error
