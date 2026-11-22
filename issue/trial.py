from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrialSegment:
    values: dict[str, int]
    events: list[dict[str, object]] = field(default_factory=list)


def open_trial(values: dict[str, int], current: TrialSegment | None = None) -> TrialSegment:
    if current is not None:
        raise ValueError('a trial is already open')
    return TrialSegment(values.copy())


def finish_trial(
    trial: TrialSegment | None,
    formal_values: dict[str, int],
    formal_events: list[dict[str, object]],
    *,
    keep: bool,
) -> None:
    if trial is None:
        raise ValueError('no trial is open')
    if keep:
        formal_values.update(trial.values)
        formal_events.extend(trial.events)
