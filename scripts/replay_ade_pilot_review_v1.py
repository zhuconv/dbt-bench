"""Short, read-only checker replay from the eight completed ADE pilot receipts.

This does not launch Harbor, dbt, a container, or a model. The original checker
request and its saved reconstruction inputs are reused byte-for-byte. New files
are review evidence only; no replacement measurement receipt is produced.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from dbt_bench import io
from difficulty_receipt_binding import verify as verify_binding


def load(reference):
    if not io.evidence_ok(reference, ROOT):
        raise ValueError("Changed evidence: " + str(reference))
    return io.read_json(ROOT / reference["path"])


def semantic_result(result):
    # The existing score adapter adds these two reporting flags after run().
    return {k: v for k, v in result.items()
            if k not in ("model_requests", "training_prohibited")}


def replay(reconciliation, trial_name, output):
    started = time.monotonic()
    reconciliation, output = Path(reconciliation), Path(output)
    if output.exists() or not output.resolve().is_relative_to("/scratch"):
        raise ValueError("Use a new SCRATCH review directory")
    report_ref = io.evidence_ref(reconciliation, ROOT)
    report = io.read_json(reconciliation)
    matches = [(task, attempt) for task in report["tasks"]
               if task["task_key"] in ("ade__ade-bench-ana-eng005", "ade__ade-bench-f1003")
               for attempt in task["attempts"]
               if Path(attempt["original_result"]["path"]).parent.name == trial_name]
    if len(matches) != 1:
        raise ValueError("Select exactly one completed pilot trial")
    task, attempt = matches[0]
    raw = load(attempt["original_result"])
    receipt = load(attempt["audit_receipt"])
    if (receipt["original_result"] != attempt["original_result"]
            or receipt["task_key"] != task["task_key"]
            or receipt["task_version"] != task["task_version"]
            or receipt["fixture_only"] is not False
            or receipt["training_prohibited"] is not True
            or receipt["model_requests"] != 0):
        raise ValueError("Receipt does not bind the actual completed pilot")
    rewards = (raw.get("verifier_result") or {}).get("rewards")
    if rewards != attempt["native_rewards"] or rewards != receipt["original_native_rewards"]:
        raise ValueError("Native rewards differ between raw result and reports")
    if trial_name.endswith("bm87w2U") and "native-deps-v3" not in attempt["audit_receipt"]["path"]:
        raise ValueError("F1003 bm87w2U must use its final native-deps-v3 receipt")
    context = verify_binding(receipt, ROOT)
    qualified = load(receipt["qualified_review"])
    overlay = load(qualified["audit"]["overlay"])
    dependencies = load(receipt["adapter_dependencies"])
    code = [*overlay["code_refs"], *dependencies["code_refs"], *dependencies["checker_dependencies"]]
    for reference in code:
        if not io.evidence_ok(reference, ROOT):
            raise ValueError("Changed frozen checker/adapter code: " + reference["path"])
    checker = ROOT / receipt["checker"]["path"]
    if not io.evidence_ok(receipt["checker"], ROOT):
        raise ValueError("Frozen checker bytes changed")
    if not any((ROOT / r["path"]).resolve() == checker.resolve()
               and r["sha256"] == receipt["checker"]["sha256"] for r in overlay["code_refs"]):
        raise ValueError("Checker is outside the qualified audit")
    request = load(receipt["checker_request"])
    previous = load(receipt["audit_result"])
    if (previous["reward"] != receipt["audit_reward"]
            or previous["status"] != receipt["audit_status"]
            or previous["reward"] != attempt["audit_reward"]):
        raise ValueError("Previously recorded audit score differs")
    module_spec = importlib.util.spec_from_file_location("pilot_frozen_checker", checker)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    checker_started = time.monotonic()
    result = module.run(request, ROOT)
    checker_elapsed = time.monotonic() - checker_started
    equal = semantic_result(result) == semantic_result(previous)
    # Recheck the three immutable entrypoints after execution.
    for reference in (attempt["original_result"], attempt["audit_receipt"], receipt["checker_request"]):
        if not io.evidence_ok(reference, ROOT):
            raise ValueError("An original input changed during replay")
    output.mkdir(parents=True)
    io.write_json(output / "checker-result.json", result)
    evidence = {
        "schema_version": 1, "review_kind": "saved_input_frozen_checker_replay",
        "task_key": task["task_key"], "task_version": task["task_version"],
        "attempt": attempt["attempt"], "trial_id": raw["id"],
        "recorded_at": datetime.now(timezone.utc).isoformat(), "hostname": socket.gethostname(),
        "reconciliation": report_ref, "original_result": attempt["original_result"],
        "original_receipt": attempt["audit_receipt"], "source_binding": receipt["source_binding"],
        "capture": context["capture_ref"], "rebuild_result": receipt["rebuild_result"],
        "qualified_review": receipt["qualified_review"], "checker": receipt["checker"],
        "checker_request": receipt["checker_request"], "prior_audit_result": receipt["audit_result"],
        "replayed_result": io.evidence_ref(output / "checker-result.json", ROOT),
        "native_rewards": rewards, "prior_audit_reward": previous["reward"],
        "replayed_audit_reward": result["reward"], "prior_audit_status": previous["status"],
        "replayed_audit_status": result["status"], "semantic_result_equal": equal,
        "comparison_omits_only_adapter_added_fields": ["model_requests", "training_prohibited"],
        "checker_reexecuted": True, "adapter_reexecuted": False,
        "source_binding_verified": True, "code_references_verified": code,
        "prior_image_reference": dependencies["image"],
        "image_rehashed_or_executed": False,
        "checker_elapsed_seconds": checker_elapsed, "total_elapsed_seconds": time.monotonic() - started,
        "execution": {"python": sys.executable, "python_version": sys.version,
                      "script": io.evidence_ref(Path(__file__), ROOT)},
        "new_model_attempt": False, "model_requests": 0, "training_prohibited": True,
        "native_executed": False, "dbt_rebuild_executed": False, "container_started": False,
        "scope": "Actually reexecuted the frozen checker against its exact previously captured request; reused original source/rebuild provenance. Not a new model attempt or replacement score. Original native reward and all saved evidence remain unchanged.",
    }
    io.write_json(output / "replay.json", evidence)
    if not equal:
        raise ValueError("Replay differs; preserve new output for review")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--trial", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    row = replay(args.reconciliation, args.trial, args.output)
    print({k: row[k] for k in ("task_key", "attempt", "native_rewards", "replayed_audit_reward",
                               "semantic_result_equal", "checker_elapsed_seconds", "total_elapsed_seconds")})
