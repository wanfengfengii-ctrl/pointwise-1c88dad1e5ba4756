from __future__ import annotations

from dataclasses import dataclass

from daily.snapshots import DaySnapshot
from issue.checks import observe_check
from issue.package import Adjustment, FrozenPackage


class EligibilityError(ValueError):
    pass


class CheckMismatch(EligibilityError):
    pass


@dataclass(frozen=True)
class PostingPlan:
    day: str
    changes: tuple[tuple[str, int], ...]


def evaluate_package(snapshot: DaySnapshot, package: FrozenPackage) -> PostingPlan:
    if snapshot.day != package.day:
        raise EligibilityError('the local snapshot belongs to another date')
    initial = snapshot.values()
    working = initial.copy()
    registered = {key.region for key in snapshot.registered}
    touched = set()
    for event in package.events:
        region = event.region
        if region not in registered:
            raise EligibilityError(f'unregistered day region {package.day} / {region}')
        touched.add(region)
        if isinstance(event, Adjustment):
            working[region] += event.after - event.before
        else:
            local = observe_check(region, event.name, event.target, working[region])
            if local.matched != event.matched:
                raise CheckMismatch(
                    f'check {event.name} in {region} changed from {event.matched} to {local.matched}'
                )
    return PostingPlan(package.day, tuple((region, working[region] - initial[region]) for region in sorted(touched)))
