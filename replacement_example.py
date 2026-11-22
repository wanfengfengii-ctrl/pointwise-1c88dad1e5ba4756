import json
from contextlib import closing
from pathlib import Path

from audit.ledger import read
from daily.supplements import supplement
from daily.warehouse import initialize_warehouse, open_warehouse
from intake.batches import ingest
from intake.stations import Station
from issue.freeze import freeze
from issue.package import encode_package
from issue.work_batch import correct
from receive.admission import accept


SAMPLES = Path(__file__).resolve().parent / "samples"


def load_sample(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def seed(db, observations):
    stations = [Station(row["station_id"], row["region"], tuple(row["units"]))
                for row in observations["stations"]]
    initialize_warehouse(db, stations)
    ingest(db, observations["batch_id"], observations["source_id"], observations["rows"])


def require_total(label, actual, expected):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected} L, got {actual} L")


def run_example(observations, scenario):
    day, region = scenario["day"], scenario["region"]
    wires = {}
    prefix_batches = {step["batch_id"] for step in scenario["review_prefix"] if "batch_id" in step}
    with closing(open_warehouse(":memory:")) as source:
        seed(source, observations)
        for step in scenario["source_steps"]:
            batch_id = step.get("batch_id")
            if batch_id in prefix_batches or batch_id == "ordinary-plus-one":
                correct(source, batch_id, day, step["operations"])
                wires[batch_id] = encode_package(freeze(source, batch_id))

    receipts = []
    with closing(open_warehouse(":memory:")) as reviewer:
        seed(reviewer, observations)
        for step in scenario["review_prefix"]:
            if "supplement_id" in step:
                label = step["supplement_id"]
                supplement(reviewer, label, day, region, step["litres"])
            else:
                label = step["acceptance_id"]
                receipt = accept(reviewer, label, wires[step["batch_id"]])
                receipts.append((receipt.receipt_number, step["total_litres"]))
            require_total(label, read(reviewer, day=day).total_litres, step["total_litres"])

        before = read(reviewer, day=day).total_litres
        accept(reviewer, "ordinary-extra", wires["ordinary-plus-one"])
        after = read(reviewer, day=day).total_litres
        require_total("ordinary-extra", after, 25)
        historical = {}
        for receipt_number, expected in receipts:
            total = read(reviewer, receipt_number=receipt_number).total_litres
            require_total(receipt_number, total, expected)
            historical[receipt_number] = total
    return {"v1_before_extra": before, "v1_after_ordinary_extra": after, "original_receipts": historical}


if __name__ == "__main__":
    result = run_example(load_sample("observations.json"), load_sample("replacement.json"))
    print(json.dumps(result, sort_keys=True))
