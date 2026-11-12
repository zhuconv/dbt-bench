#!/usr/bin/env python3
"""Exercise the installed or preserved native Qwen converter; never certify SFT readiness."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import argparse
from copy import deepcopy
import importlib.metadata
import importlib.util
import inspect
import json

from dbt_bench.io import evidence_ref, file_hash, read_json, write_json
from dbt_bench.trace import audit_trace, harbor_schema_validator

ORIGINAL_SHA256 = "488bf5a6b607e0d3cccf384ff56a8bac14003ca8a0a6013b5a38b95dab492b2e"


def fixtures():
    base = read_json(ROOT / "tests/trace_fidelity/error-only.json")["events"]
    cases = []

    def add(name, events, expected="complete"):
        cases.append({"name": name, "events": events, "expected_fidelity": expected})

    add("error_only", deepcopy(base))
    for name, value in [("tool_success", "done"), ("empty_output", ""), ("long_output", "z" * 100_000)]:
        events = deepcopy(base)
        events[-1]["message"]["parts"][0]["functionResponse"]["response"] = {"output": value}
        add(name, events)
    events = deepcopy(base)
    events[1]["message"]["parts"].append({"functionCall": {"id": "second", "name": "other", "args": {}}})
    events[-1]["message"]["parts"].insert(0, {"functionResponse": {"id": "second", "response": {"output": "second result"}}})
    add("parallel_reversed_results", events)
    events = deepcopy(base)
    events[1]["message"]["parts"].insert(0, {"text": "reasoning fixture", "thought": True})
    add("reasoning", events)
    # An output-only fallback is not a general proof of fidelity. These must remain gaps.
    events = deepcopy(base)
    events[-1]["message"]["parts"][0]["functionResponse"]["response"]["output"] = "partial output"
    add("output_plus_error_not_certified", events, "incomplete")
    for kind in ("retry", "compression"):
        events = deepcopy(base)
        events.append({"type": kind, "message": {"parts": [{"text": "fixture metadata"}]}})
        add(kind + "_requires_explicit_mapping", events, "incomplete")
    add("abnormal_termination", deepcopy(base[:-1]), "incomplete")
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    if importlib.metadata.version("harbor") != "0.25.0":
        raise ValueError("Harbor version mismatch")
    if args.original:
        source = ROOT / ".cache/runtime-originals" / (ORIGINAL_SHA256 + ".py")
        if file_hash(source) != ORIGINAL_SHA256:
            raise ValueError("Preserved original source mismatch")
        spec = importlib.util.spec_from_file_location("dbt_bench_original_qwen", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        QwenCode = module.QwenCode
    else:
        from harbor.agents.installed.qwen_code import QwenCode
        source = Path(inspect.getfile(QwenCode))
    agent = object.__new__(QwenCode)
    agent.model_name = "openai/fixture"
    rows = []
    for case in fixtures():
        atif = agent._convert_events_to_trajectory(case["events"]).model_dump(mode="json")
        audit = audit_trace(case["events"], atif, schema_validator=harbor_schema_validator)
        actual = audit["content_fidelity"]
        row = {"name": case["name"], "expected_fidelity": case["expected_fidelity"], "audit": audit,
               "passed": actual == case["expected_fidelity"] and audit["schema_valid"] is True}
        artifact = args.output_dir / (case["name"] + ".json")
        write_json(artifact, {"fixture": case, "atif": atif, "result": row})
        row["evidence"] = evidence_ref(artifact, ROOT)
        rows.append(row)
    result = {"schema_version": 1, "harbor_version": "0.25.0", "adapter_source_sha256": file_hash(source),
              "fixture_kind": "synthetic_events_executed_by_actual_converter", "benchmark_trials": 0,
              "all_checks_passed": all(r["passed"] for r in rows), "cases": rows,
              "full_content_conformance": False, "model_context_capture": "unknown",
              "scope": "Basic message/error/ID preservation and detection of unsupported events; not all log shapes or SFT certification"}
    write_json(args.output_dir / "summary.json", result)
    print(json.dumps({"checks_passed": sum(r["passed"] for r in rows), "checks": len(rows),
                      "summary": str(args.output_dir / "summary.json")}, indent=2))
    return 0 if result["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
