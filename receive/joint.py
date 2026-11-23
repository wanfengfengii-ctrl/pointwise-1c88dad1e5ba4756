"""Joint source/reviewer verification of in-place corrections on independent, on-disk warehouses.

The source warehouse is fully written, issues every package, and is then closed.  The
reviewer warehouse is opened separately; a second reviewer exercises the pre-upgrade
(legacy) day, and a commit failure is triggered on a third.  Every assertion raises
``AssertionError`` so the caller can exit non-zero.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.ledger import read
from daily.supplements import supplement
from daily.warehouse import initialize_warehouse, open_warehouse
from intake.batches import ingest
from intake.stations import Station
from issue.freeze import freeze, freeze_replacement
from issue.package import encode_package
from issue.work_batch import correct
from receive.admission import (
    AdmissionError,
    CommitError,
    IncompatiblePackage,
    accept,
    accept_replacement,
)
from receive.receipts import find_receipt_by_acceptance


def _load(samples: Path, name: str) -> dict:
    return json.loads((samples / name).read_text(encoding="utf-8"))


def _stations(observations: dict) -> tuple[Station, ...]:
    return tuple(Station(row["station_id"], row["region"], tuple(row["units"]))
                 for row in observations["stations"])


def _seed(db: sqlite3.Connection, observations: dict) -> None:
    initialize_warehouse(db, _stations(observations))
    ingest(db, observations["batch_id"], observations["source_id"], observations["rows"])


def _require(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _issue_all(source: sqlite3.Connection, observations: dict, scenario: dict) -> tuple[dict, dict]:
    _seed(source, observations)
    wires: dict[str, bytes] = {}
    replacements: dict[str, bytes] = {}
    for step in scenario["source_steps"]:
        if "supplement_id" in step:
            supplement(source, step["supplement_id"], scenario["day"], scenario["region"], step["litres"])
            continue
        batch_id = step["batch_id"]
        correct(source, batch_id, scenario["day"], step["operations"])
        if "replaces" in step:
            replacements[batch_id] = freeze_replacement(source, step["replaces"], batch_id)
        else:
            wires[batch_id] = encode_package(freeze(source, batch_id))
    return wires, replacements


def _run_prefix(reviewer: sqlite3.Connection, observations: dict, scenario: dict, wires: dict) -> None:
    _seed(reviewer, observations)
    for step in scenario["review_prefix"]:
        if "supplement_id" in step:
            supplement(reviewer, step["supplement_id"], scenario["day"], scenario["region"], step["litres"])
        else:
            accept(reviewer, step["acceptance_id"], wires[step["batch_id"]])
        _require(step.get("supplement_id") or step.get("acceptance_id"),
                 read(reviewer, day=scenario["day"]).total_litres, step["total_litres"])


def _disconnected_and_regrant(root: Path, observations: dict, scenario: dict) -> dict:
    """Source is written and closed before the reviewer runs; reopening re-issues identical bytes."""
    day = scenario["day"]
    source_path = root / "source.sqlite"
    with closing(open_warehouse(source_path)) as source:
        wires, replacements = _issue_all(source, observations, scenario)
    with closing(open_warehouse(source_path)) as source:
        _require("reopened resume A5", freeze_replacement(source, "A", "A5"), replacements["A5"])

    reviewer_path = root / "reviewer.sqlite"
    with closing(open_warehouse(reviewer_path)) as reviewer:
        _run_prefix(reviewer, observations, scenario, wires)
        before = read(reviewer, day=day)
        _require("prefix live", before.total_litres, 24)
        try:
            accept_replacement(reviewer, "a4", "RC-z-first", replacements["A4"])
        except IncompatiblePackage:
            pass
        else:
            raise AssertionError("A4 (+4 L) must flip B's check and be refused")
        _require("refusal keeps live", read(reviewer, day=day), before)
        _require("refusal leaves no receipt", find_receipt_by_acceptance(reviewer, "a4"), None)

    with closing(open_warehouse(reviewer_path)) as reviewer:
        a5 = accept_replacement(reviewer, "a5", "RC-z-first", replacements["A5"])
        _require("A5 receipt", a5.receipt_number, "RC-a5")
        _require("A5 live", read(reviewer, day=day).total_litres, 26)
        accept(reviewer, "m-third", wires["C"])
        _require("C live", read(reviewer, day=day).total_litres, 26)
        b2 = accept_replacement(reviewer, "b2", "RC-a-second", replacements["B2"])
        _require("B2 live", read(reviewer, day=day).total_litres, 26)
        _require("old z-first fixed", read(reviewer, receipt_number="RC-z-first").total_litres, 15)
        _require("old a-second fixed", read(reviewer, receipt_number="RC-a-second").total_litres, 20)
        live = read(reviewer, day=day)

    with closing(open_warehouse(reviewer_path)) as reviewer:
        _require("regrant same id", accept_replacement(reviewer, "b2", "RC-a-second", replacements["B2"]), b2)
        _require("regrant stable", read(reviewer, day=day), live)
        _require("new receipt claimable", read(reviewer, receipt_number=b2.receipt_number), live)
    return {"live_after_replacements": live.total_litres, "old_receipts": {
        "RC-z-first": 15, "RC-a-second": 20}, "replacement_receipts": [a5.receipt_number, b2.receipt_number]}


def _legacy_day(root: Path, observations: dict, scenario: dict) -> dict:
    """A pre-upgrade day keeps accepting and reporting, but can never be replaced."""
    day = scenario["day"]
    source_path = root / "legacy-source.sqlite"
    with closing(open_warehouse(source_path)) as source:
        wires, replacements = _issue_all(source, observations, scenario)
    reviewer_path = root / "legacy-reviewer.sqlite"
    with closing(open_warehouse(reviewer_path)) as reviewer:
        _seed(reviewer, observations)
        accept(reviewer, "z-first", wires["A"])
        # Drop the historical posting basis as if the day had been recorded before the upgrade.
        reviewer.execute("DELETE FROM program_log WHERE kind = 'observation_batch'")
        reviewer.commit()
        supplement(reviewer, "late", day, "东区", 2)
        accept(reviewer, "late-ordinary", wires["ordinary-plus-one"])
        _require("legacy live", read(reviewer, day=day).total_litres, 16)
        before = read(reviewer, day=day)
        try:
            accept_replacement(reviewer, "a5", "RC-z-first", replacements["A5"])
        except AdmissionError:
            pass
        else:
            raise AssertionError("a day without review basis must not be replaced")
        _require("legacy refusal unchanged", read(reviewer, day=day), before)
    return {"legacy_live": 16, "legacy_replacement": "refused"}


def _commit_failure(root: Path, observations: dict, scenario: dict) -> dict:
    """A failed commit leaves no receipt, link or balance change, and the id can be reused."""
    day = scenario["day"]
    source_path = root / "commit-source.sqlite"
    with closing(open_warehouse(source_path)) as source:
        wires, replacements = _issue_all(source, observations, scenario)
    reviewer_path = root / "commit-reviewer.sqlite"
    with closing(open_warehouse(reviewer_path)) as reviewer:
        _seed(reviewer, observations)
        accept(reviewer, "z-first", wires["A"])
        before = read(reviewer, day=day)
        reviewer.execute("CREATE TABLE gate_parent (id INTEGER PRIMARY KEY)")
        reviewer.execute("CREATE TABLE gate_child (pid INTEGER REFERENCES gate_parent(id) "
                         "DEFERRABLE INITIALLY DEFERRED)")
        reviewer.execute("CREATE TRIGGER gate AFTER INSERT ON audit_views "
                         "BEGIN INSERT INTO gate_child VALUES (1); END")
        try:
            accept_replacement(reviewer, "a5", "RC-z-first", replacements["A5"])
        except CommitError:
            pass
        else:
            raise AssertionError("the deferred foreign key must fail the commit")
        _require("failed commit leaves balance", read(reviewer, day=day), before)
        _require("failed commit leaves no receipt", find_receipt_by_acceptance(reviewer, "a5"), None)
        _require("failed commit leaves no link",
                 reviewer.execute("SELECT COUNT(*) FROM replacement_links").fetchone()[0], 0)
        reviewer.execute("DROP TRIGGER gate")
    with closing(open_warehouse(reviewer_path)) as reviewer:
        receipt = accept_replacement(reviewer, "a5", "RC-z-first", replacements["A5"])
        _require("retry after failure", read(reviewer, day=day).total_litres, 15)
    return {"commit_failure": "clean", "retried_receipt": receipt.receipt_number}


def joint_verification(samples: Path) -> dict:
    observations = _load(samples, "observations.json")
    scenario = _load(samples, "replacement.json")
    with TemporaryDirectory(prefix="chengpu-joint-") as tmp:
        root = Path(tmp)
        output = {"disconnected": _disconnected_and_regrant(root, observations, scenario),
                  "legacy": _legacy_day(root, observations, scenario),
                  "recovery": _commit_failure(root, observations, scenario)}
    return output
