from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from daily.calendar import validate_day
from daily.supplements import Supplement, read_day_supplements
from intake.records import Observation, read_day_observations
from receive.posting import PostedAdjustment, read_posted_adjustments


@dataclass(frozen=True)
class RegionBalance:
    region: str
    litres: int
    missing_reads: int
    valid_reads: int
    contributions: tuple[Observation | Supplement | PostedAdjustment, ...]


def aggregate_day(db: sqlite3.Connection, day: str) -> tuple[RegionBalance, ...]:
    day = validate_day(day)
    rows_by_region = defaultdict(list)
    for rows in (read_day_observations(db, day), read_day_supplements(db, day), read_posted_adjustments(db, day)):
        for row in rows:
            rows_by_region[row.region].append(row)

    balances = []
    for region, rows in sorted(rows_by_region.items()):
        litres = missing_reads = valid_reads = 0
        for row in rows:
            if isinstance(row, Observation):
                if row.litres is None:
                    missing_reads += 1
                else:
                    valid_reads += 1
                    litres += row.litres
            else:
                litres += row.delta
        balances.append(RegionBalance(region, litres, missing_reads, valid_reads, tuple(rows)))
    return tuple(balances)
