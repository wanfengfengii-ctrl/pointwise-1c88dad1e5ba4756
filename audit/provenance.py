from __future__ import annotations

from dataclasses import asdict

from daily.snapshots import DaySnapshot
from daily.supplements import Supplement
from intake.records import Observation
from receive.posting import PostedAdjustment


def _reference(contribution: Observation | Supplement | PostedAdjustment) -> dict[str, object]:
    fields = asdict(contribution)
    if isinstance(contribution, Observation):
        return {'kind': 'observation', **fields, 'missing': contribution.litres is None}
    if isinstance(contribution, Supplement):
        return {'kind': 'supplement', **fields}
    return {'kind': 'posted_adjustment', **fields}


def collect_provenance(snapshot: DaySnapshot) -> dict[str, tuple[dict[str, object], ...]]:
    return {
        balance.region: tuple(_reference(contribution) for contribution in balance.contributions)
        for balance in snapshot.balances
    }
