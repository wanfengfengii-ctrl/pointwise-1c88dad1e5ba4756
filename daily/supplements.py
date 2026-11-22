from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from daily.calendar import require_day_region, validate_day
from intake.stations import require_identifier
from receive.transaction import write_transaction


class SupplementError(ValueError):
    pass


@dataclass(frozen=True)
class Supplement:
    supplement_id: str
    day: str
    region: str
    delta: int


def initialize_supplements(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS supplements (
            supplement_id TEXT PRIMARY KEY NOT NULL,
            day TEXT NOT NULL,
            region TEXT NOT NULL,
            delta TEXT NOT NULL
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS supplements_by_day ON supplements(day)')


def _from_row(row: tuple) -> Supplement:
    supplement_id, day, region, delta = row
    return Supplement(supplement_id, day, region, int(delta))


def supplement(db: sqlite3.Connection, supplement_id: str, day: str, region: str, litres: int) -> Supplement:
    try:
        require_identifier(supplement_id, 'supplement_id')
        if type(litres) is not int:
            raise ValueError('supplement must use integer litres')
        with write_transaction(db):
            key = require_day_region(db, day, region)
            item = Supplement(supplement_id, key.day, key.region, litres)
            row = db.execute('''
                SELECT supplement_id, day, region, delta FROM supplements WHERE supplement_id = ?
            ''', (supplement_id,)).fetchone()
            if row is not None:
                if _from_row(row) != item:
                    raise ValueError(f'conflicting content for supplement {supplement_id}')
                return item
            db.execute('''
                INSERT INTO supplements (supplement_id, day, region, delta) VALUES (?, ?, ?, ?)
            ''', (item.supplement_id, item.day, item.region, str(item.delta)))
            return item
    except ValueError as error:
        raise SupplementError(str(error)) from error


def read_day_supplements(db: sqlite3.Connection, day: str) -> tuple[Supplement, ...]:
    rows = db.execute('''
        SELECT supplement_id, day, region, delta FROM supplements WHERE day = ? ORDER BY supplement_id
    ''', (validate_day(day),))
    return tuple(_from_row(row) for row in rows)
