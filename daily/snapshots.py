from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from daily.aggregation import RegionBalance, aggregate_day
from daily.calendar import DayRegion, list_day_regions, validate_day
from receive.receipts import has_day_receipt


@dataclass(frozen=True)
class DaySnapshot:
    day: str
    registered: tuple[DayRegion, ...]
    balances: tuple[RegionBalance, ...]
    established: bool

    def values(self) -> dict[str, int]:
        values = dict.fromkeys((key.region for key in self.registered), 0)
        values.update((balance.region, balance.litres) for balance in self.balances)
        return values


def _read_cut(db: sqlite3.Connection, day: str) -> DaySnapshot:
    registered = list_day_regions(db, day)
    balances = aggregate_day(db, day)
    return DaySnapshot(day, registered, balances, bool(balances) or has_day_receipt(db, day))


def take_day_snapshot(db: sqlite3.Connection, day: str) -> DaySnapshot:
    day = validate_day(day)
    if db.in_transaction:
        return _read_cut(db, day)
    db.execute('BEGIN')
    try:
        return _read_cut(db, day)
    finally:
        db.rollback()
