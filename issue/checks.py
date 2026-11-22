from dataclasses import dataclass

from intake.stations import require_identifier


@dataclass(frozen=True)
class CheckObservation:
    region: str
    name: str
    target: int
    matched: bool


def observe_check(region: str, name: str, target: int, at_litres: int) -> CheckObservation:
    require_identifier(region, 'region')
    require_identifier(name, 'check name')
    if type(target) is not int or type(at_litres) is not int:
        raise ValueError('check target and reading must use integer litres')
    return CheckObservation(region, name, target, at_litres == target)
