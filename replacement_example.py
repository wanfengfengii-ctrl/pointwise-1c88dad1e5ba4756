"""Run the joint source/reviewer replacement sample and print the observed results."""
import json
from contextlib import closing
from pathlib import Path

from audit.ledger import read
from daily.supplements import supplement
from daily.warehouse import initialize_warehouse, open_warehouse
from intake.batches import ingest
from intake.stations import Station
from issue.freeze import freeze, freeze_replacement
from issue.package import encode_package
from issue.work_batch import correct
from receive.admission import IncompatiblePackage, accept, accept_replacement


SAMPLES = Path(__file__).resolve().parent / "samples"


def load_sample(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def seed(db, observations):
    stations = [Station(row["station_id"], row["region"], tuple(row["units"]))
                for row in observations["stations"]]
    initialize_warehouse(db, stations)
    ingest(db, observations["batch_id"], observations["source_id"], observations["rows"])


def issue_all(observations, scenario, source_path=":memory:"):
    day, region = scenario["day"], scenario["region"]
    wires, replacements = {}, {}
    with closing(open_warehouse(source_path)) as source:
        seed(source, observations)
        for step in scenario["source_steps"]:
            if "supplement_id" in step:
                supplement(source, step["supplement_id"], day, region, step["litres"])
                continue
            batch_id = step["batch_id"]
            correct(source, batch_id, day, step["operations"])
            if "replaces" in step:
                replacements[batch_id] = freeze_replacement(source, step["replaces"], batch_id)
            else:
                wires[batch_id] = encode_package(freeze(source, batch_id))
    return wires, replacements


def require_total(label, actual, expected):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected} L, got {actual} L")


def run_example(observations, scenario):
    day, region = scenario["day"], scenario["region"]
    wires, replacements = issue_all(observations, scenario)

    result = {"prefix": [], "refusals": [], "live_after_validity": None,
              "new_receipts": [], "historical": {}, "regrant": None}
    with closing(open_warehouse(":memory:")) as reviewer:
        seed(reviewer, observations)
        for step in scenario["review_prefix"]:
            if "supplement_id" in step:
                supplement(reviewer, step["supplement_id"], day, region, step["litres"])
            else:
                accept(reviewer, step["acceptance_id"], wires[step["batch_id"]])
            require_total(step.get("supplement_id") or step.get("acceptance_id"),
                          read(reviewer, day=day).total_litres, step["total_litres"])
            result["prefix"].append({"step": step.get("supplement_id") or step.get("acceptance_id"),
                                     "total_litres": step["total_litres"]})

        before = read(reviewer, day=day)
        try:
            accept_replacement(reviewer, "a4", "RC-z-first", replacements["A4"])
        except IncompatiblePackage:
            result["refusals"].append({"acceptance_id": "a4",
                                       "after_total_litres": read(reviewer, day=day).total_litres})
        else:
            raise AssertionError("a4 should have been refused")
        require_total("after-a4", read(reviewer, day=day).total_litres, before.total_litres)

        a5 = accept_replacement(reviewer, "a5", "RC-z-first", replacements["A5"])
        result["new_receipts"].append({"receipt_number": a5.receipt_number,
                                       "source_batch": a5.source_batch,
                                       "package_id": a5.package_id,
                                       "total_litres": read(reviewer, day=day).total_litres})
        accept(reviewer, "m-third", wires["C"])
        require_total("m-third", read(reviewer, day=day).total_litres, 26)

        b2 = accept_replacement(reviewer, "b2", "RC-a-second", replacements["B2"])
        result["new_receipts"].append({"receipt_number": b2.receipt_number,
                                       "source_batch": b2.source_batch,
                                       "package_id": b2.package_id,
                                       "total_litres": read(reviewer, day=day).total_litres})
        result["live_after_validity"] = read(reviewer, day=day).total_litres
        for number in ("RC-z-first", "RC-a-second"):
            result["historical"][number] = read(reviewer, receipt_number=number).total_litres
        result["regrant"] = accept_replacement(reviewer, "b2", "RC-a-second", replacements["B2"]).receipt_number
    return result


if __name__ == "__main__":
    print(json.dumps(run_example(load_sample("observations.json"), load_sample("replacement.json")),
                     ensure_ascii=False, sort_keys=True))
