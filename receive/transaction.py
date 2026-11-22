from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator


class CommitFailure(RuntimeError):
    pass


@contextmanager
def write_transaction(db: sqlite3.Connection, *, join: bool = False) -> Iterator[None]:
    if join and db.in_transaction:
        yield
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        try:
            db.commit()
        except sqlite3.Error as error:
            raise CommitFailure(f'SQLite COMMIT failed: {error}') from error
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise


posting_transaction = write_transaction
