from __future__ import annotations

import sqlite3

from intake.stations import require_identifier
from issue.package import FrozenPackage, decode_event, decode_package, encode_package
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
