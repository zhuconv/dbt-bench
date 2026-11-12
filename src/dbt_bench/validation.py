"""Evidence ledger and isolated Harbor reference/case execution."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
import uuid

from .catalog import load_catalog
from .backends import (BACKENDS, binding_identity, configure_backend, find_apptainer,
                       harbor_process_env, require_runtime, resolve_task)
from .io import (digest, evidence_ok, evidence_ref, file_hash, read_json, read_jsonl,
                 tree_hash, write_json, write_jsonl, write_text, contained)
from .prepare import check_harbor, validate_layout

PROTOCOL = "dbt-verifier-validation-v1"
CHECKS = ("assets", "contract", "environment_agent", "environment_verifier", "cross_context",
          "agent_visibility", "reference", "reference_repeat", "nop", "equivalent",
          "independent_semantics", "negatives", "comparator", "stability")
REF_KINDS = {"full_gold", "sampled_gold", "independent_recompute", "aggregate_assertions", "combined", "unknown"}
WITNESS_KINDS = {"built_from_inputs", "copy_gold", "unknown"}


def task_dir(root, row):
    return Path(root) / "validation" / row["source"] / row["upstream_task_id"]


def initial_contract(row, risks):
    return {
        "schema_version": 1, "task_key": row["task_key"], "review_status": "pending_assets",
        "workflow": "unknown", "reference_kind": "unknown", "witness_kind": "unknown",
        "inputs": [], "deliverables": [], "requirements": [],
        "missing_contract_work": ["Read pinned instruction/setup/project docs/reference/verifier",
                                  "Locate input/output paths, tables, keys and legal equivalences",
                                  "Map every critical requirement to native checks or a declared gap"],
        "risk_ids": [r["id"] for r in risks if row["task_key"] in r.get("tasks", []) or row["source"] in r.get("sources", [])],
        "native_success": {"metric": "reward", "equals": 1.0, "reviewed": False},
        "review_evidence": [], "checks": {}, "audit": None,
        "notes": ["Historical findings are reproduction targets, not conclusions for this revision."],
    }


def identity(root, row, contract_path, backend="docker"):
    root = Path(root)
    path = root / row["run_path"]
    directory = contract_path.parent
    prepared = root / "catalog/prepared" / f"{row['task_key']}.json"
    environment = directory / "environment.json"
    return {
        "protocol": PROTOCOL, "task_key": row["task_key"], "backend": backend,
        "backend_binding": binding_identity(root, row, backend),
        "source_lock": file_hash(root / "sources.lock.yaml"),
        "task": tree_hash(path) if path.is_dir() else None,
        "catalog_task": digest(row), "prepared": file_hash(prepared) if prepared.exists() else None,
        "contract": file_hash(contract_path),
        "cases": tree_hash(directory / "cases") if (directory / "cases").is_dir() else None,
        "environment_data": file_hash(environment) if environment.exists() else None,
        "verifier": tree_hash(path / "tests") if (path / "tests").is_dir() else None,
        "run_config": file_hash(root / "configs/validation.yaml"),
        # Trace conversion and reporting only consume immutable trial artifacts.
        # They have their own version/hash evidence and never execute this protocol.
        "implementation": {p.name: file_hash(p) for p in sorted((root / "src/dbt_bench").glob("*.py")) if p.name not in {"report.py", "trace.py"}},
        "probe": file_hash(root / "scripts/context_probe.py"),
    }


def reusable(record, current_identity, root):
    """Only exact identity plus complete, nonempty evidence can be resumed."""
    if record.get("identity") != current_identity:
        return False
    refs = record.get("evidence", [])
    return bool(refs) and all(evidence_ok(ref, root) for ref in refs)


def review_inputs(current_identity):
    """Non-circular scope that a reviewer binds every check to before certification."""
    return {k: v for k, v in current_identity.items() if k != "contract"}


def _check(value, root):
    if not value:
        return {"status": "pending", "reason": "Not executed/reviewed", "evidence": []}
    value = dict(value)
    if value.get("status") not in {"pass", "fail", "gap", "pending", "not_applicable"}:
        raise ValueError("Invalid check status")
    if value["status"] != "pending":
        if not value.get("reason") or not value.get("evidence") or not all(evidence_ok(e, root) for e in value["evidence"]):
            return {"status": "pending", "reason": "Evidence missing or changed", "evidence": []}
    return value


def adjudicate(contract, checks, *, identity_complete, root, target="native"):
    requirements = contract.get("requirements", [])
    required = [r for r in requirements if r.get("critical", True)]
    gaps = []
    failures = []
    for req in required:
        coverage = req.get("coverage", {}).get(target, {})
        if coverage.get("status") in {"missed_wrong", "rejected_legal"}:
            proof = coverage.get("independent_proof")
            native = coverage.get("checker_evidence")
            if (evidence_ok(proof, root) and evidence_ok(native, root) and coverage.get("case_validity") == "business_error") or (
                coverage.get("status") == "rejected_legal" and evidence_ok(proof, root) and evidence_ok(native, root)
                and coverage.get("case_validity") == "legal_equivalent"
            ):
                failures.append(req["id"])
        if coverage.get("status") != "covered" or not coverage.get("evidence") or not all(evidence_ok(e, root) for e in coverage["evidence"]):
            gaps.append(req["id"])
    # Confirmed task/environment faults require a separate evidence-backed diagnosis.
    diagnoses = contract.get("diagnoses", [])
    broken = [d for d in diagnoses if d.get("target", "native") == target and d.get("kind") == "broken"
              and d.get("reason") and d.get("evidence") and all(evidence_ok(e, root) for e in d["evidence"])]
    if broken:
        return "broken", gaps, [d["reason"] for d in broken]
    if failures:
        return "weak", gaps, [f"Confirmed verifier gap: {r}" for r in failures]
    complete = (identity_complete and bool(required) and not gaps
                and contract.get("review_status") == "reviewed"
                and contract.get("witness_kind") == "built_from_inputs"
                and contract.get("reference_kind") in REF_KINDS - {"unknown"}
                and all(checks[c]["status"] in {"pass", "not_applicable"} for c in CHECKS)
                and all(checks[c]["status"] == "pass" for c in ("assets", "contract", "reference", "reference_repeat", "independent_semantics", "negatives", "stability")))
    return ("validated" if complete else "pending"), gaps, []


def build_record(root, row, contract_path, execution=None, backend="docker"):
    root = Path(root)
    contract = read_json(contract_path)
    if contract.get("task_key") != row["task_key"]:
        raise ValueError("Contract task ID mismatch")
    if contract.get("reference_kind") not in REF_KINDS or contract.get("witness_kind") not in WITNESS_KINDS:
        raise ValueError("Unknown reference/witness kind")
    ids = [r["id"] for r in contract.get("requirements", [])]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate requirement IDs")
    current = identity(root, row, contract_path, backend)
    review_current = contract.get("reviewed_inputs") == review_inputs(current)
    checks = {name: _check(contract.get("checks", {}).get(name) if review_current else None, root) for name in CHECKS}
    path = root / row["run_path"]
    blockers = []
    backend_ready = True
    if not path.exists():
        blockers.append("Pinned task assets unavailable; run prepare tasks with network access or a verified checkout")
    if backend == "docker" and not shutil.which("docker"):
        blockers.append("Docker unavailable for selected backend; Apptainer can be selected with --backend apptainer after SIF preparation")
    if backend == "apptainer":
        if not find_apptainer():
            blockers.append("Apptainer not found in PATH or supported cluster module location")
        try:
            resolve_task(root, row, backend)
        except (ValueError, OSError) as exc:
            backend_ready = False
            blockers.append(str(exc))
    try:
        importlib.metadata.version("harbor")
    except importlib.metadata.PackageNotFoundError:
        blockers.append("Harbor runtime not installed")
    if path.exists():
        try:
            validate_layout(path)
            if current["task"] != row["adapted_task_hash"]:
                blockers.append("Task changed since prepare")
        except (ValueError, OSError) as exc:
            blockers.append(str(exc))
    # A source-layout check alone cannot certify external databases/build effects.
    complete_identity = (all(current[k] for k in ("task", "prepared", "environment_data", "verifier"))
                         and current["task"] == row["adapted_task_hash"] and backend_ready)
    adjudication_contract = contract if review_current else {**contract, "diagnoses": [], "requirements": [
        {**r, "coverage": {}} for r in contract.get("requirements", [])]}
    native, gaps, reasons = adjudicate(adjudication_contract, checks, identity_complete=complete_identity, root=root)
    audit_contract = contract.get("audit")
    audit = {"conclusion": "pending", "checker_version": None, "checks": {}, "uncovered_requirements": ids,
             "reason": "No separately validated audit checker"}
    if audit_contract:
        audit_current = audit_contract.get("reviewed_inputs") == review_inputs(current)
        audit_checks = {name: _check(audit_contract.get("checks", {}).get(name) if audit_current else None, root) for name in CHECKS}
        merged_audit = {**contract, **audit_contract}
        if not audit_current:
            merged_audit = {**merged_audit, "diagnoses": [], "requirements": [{**r, "coverage": {}} for r in merged_audit.get("requirements", [])]}
        conclusion, audit_gaps, audit_reasons = adjudicate(merged_audit, audit_checks,
                  identity_complete=complete_identity and bool(audit_contract.get("checker_version")), root=root, target="audit")
        audit = {"conclusion": conclusion, "checker_version": audit_contract.get("checker_version"),
                 "checks": audit_checks, "uncovered_requirements": audit_gaps, "reasons": audit_reasons}
    refs = [evidence_ref(contract_path, root)]
    refs += [e for check in checks.values() for e in check["evidence"]]
    def collect_refs(value):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                return [value]
            return [r for v in value.values() for r in collect_refs(v)]
        if isinstance(value, list):
            return [r for v in value for r in collect_refs(v)]
        return []
    refs.extend(collect_refs(contract))
    if execution:
        refs.extend(execution.get("evidence", []))
    return {"schema_version": 1, "task_key": row["task_key"], "identity": current,
            "identity_hash": digest(current), "review_current": review_current, "protocol": PROTOCOL,
            "reference_kind": contract["reference_kind"], "witness_kind": contract["witness_kind"],
            "native": {"conclusion": native, "checks": checks, "uncovered_requirements": gaps,
                       "reasons": reasons, "scope": "Declared critical business requirements and protocol v1"},
            "audit": audit, "blockers": blockers,
            "pending_work": contract.get("missing_contract_work", []) + [c for c in CHECKS if checks[c]["status"] == "pending"],
            "evidence": refs, "execution": execution,
            "sampling_eligible": native == "validated" or native == "weak" and audit["conclusion"] == "validated"}


def _wrap_case(task_copy, case_dir, case):
    """Stage a reviewed script and independent proof ahead of the unchanged native verifier."""
    cfg = tomllib.loads((task_copy / "task.toml").read_text())
    mode = cfg.get("verifier", {}).get("environment", "shared")
    if isinstance(mode, dict):
        mode = mode.get("mode", "shared")
    if mode != "shared" or cfg.get("verifier", {}).get("environment_mode", "shared") != "shared":
        raise ValueError("Case execution in a separate verifier needs a reviewed transfer contract; unsupported")
    if case["kind"] not in {"negative", "equivalent", "witness"}:
        raise ValueError("Unknown case kind")
    if not case.get("requirement_ids") or not case.get("independent_derivation"):
        raise ValueError("Case must declare requirements and an independent derivation")
    if not case.get("reviewed"):
        raise ValueError("Case has not been reviewed")
    solve = task_copy / "solution/solve.sh"
    if solve.exists():
        solve.rename(task_copy / "solution/native-solve.sh")
    payload = task_copy / "solution/validation-case"
    shutil.copytree(case_dir, payload)
    import shlex
    submission = contained(payload, case["submission_script"])
    proof = contained(payload, case["proof_script"])
    if not submission.is_file() or not proof.is_file():
        raise ValueError("Case submission/proof script missing")
    # Proof script must write proof.json and exit nonzero for execution failure/equivalence.
    # No grader result is available when this proof is generated.
    solve.write_text("#!/bin/bash\nset -euo pipefail\n"
                     "export DBT_BENCH_PROOF=/logs/agent/case-proof.json\n"
                     + ("bash /solution/native-solve.sh\n" if case.get("run_native_reference") else "")
                     + f"bash {shlex.quote('/solution/validation-case/' + case['submission_script'])}\n"
                     + f"bash {shlex.quote('/solution/validation-case/' + case['proof_script'])}\n")
    native = task_copy / "tests/test.sh"
    native.rename(task_copy / "tests/native-test.sh")
    expected = "business_error" if case["kind"] == "negative" else "legal_equivalent" if case["kind"] == "equivalent" else "built_from_inputs"
    gate = ("import json,hashlib\nfrom pathlib import Path\n"
            "p=Path('/logs/agent/case-proof.json')\n"
            "d=json.loads(p.read_text())\n"
            f"assert d['validity']=={expected!r}\n"
            "assert d['derivation']\n"
            "for field in ('submission_artifacts','input_artifacts'):\n"
            "    assert d[field]\n"
            "    for ref in d[field]:\n"
            "        h=hashlib.sha256()\n"
            "        with open(ref['path'],'rb') as f:\n"
            "            for block in iter(lambda:f.read(1048576),b''): h.update(block)\n"
            "        assert h.hexdigest()==ref['sha256'], 'Proof artifact changed'\n")
    (task_copy / "tests/proof_gate.py").write_text(gate)
    native.write_text("#!/bin/bash\nset -euo pipefail\nrm -f /logs/verifier/reward.txt /logs/verifier/reward.json\npython3 /tests/proof_gate.py\nexec bash /tests/native-test.sh\n")


def run_native(root, row, *, case_file=None, resume=False, backend="docker"):
    """Harbor owns trial scheduling and retries. This builds one isolated validation job."""
    root = Path(root).resolve()
    path = resolve_task(root, row, backend)
    validate_layout(path)
    runtime = require_runtime(backend)
    command = shutil.which("harbor")
    if not command:
        raise RuntimeError("Harbor unavailable; native execution not attempted")
    cfg = read_json(root / "configs/validation.yaml")
    configure_backend(cfg, backend)
    label = "reference-nop" if case_file is None else Path(case_file).stem
    # Each invocation gets a fresh task copy and job; previous artifacts remain immutable.
    run_id = f"{label}-{uuid.uuid4().hex[:12]}"
    directory = root / "artifacts/validation" / row["task_key"] / run_id
    directory.mkdir(parents=True)
    copied = directory / "task" / row["task_key"]
    shutil.copytree(path, copied, symlinks=True)
    case = None
    if case_file:
        case_file = Path(case_file)
        case = read_json(case_file)
        _wrap_case(copied, case_file.parent, case)
        cfg["agents"] = [{"name": "oracle", "env": {"DB_TYPE": "duckdb"}}]
        cfg["n_attempts"] = 1
    cfg["tasks"] = [{"path": str(copied)}]
    cfg["jobs_dir"] = str(directory / "jobs")
    cfg["job_name"] = run_id
    cfg = check_harbor(cfg, [copied])
    config_path = directory / "config.yaml"
    write_json(config_path, cfg)
    with (directory / "harbor.log").open("w") as log:
        result = subprocess.run([command, "run", "-c", str(config_path)], stdout=log,
                                stderr=subprocess.STDOUT, env=harbor_process_env(backend))
    outputs = sorted((directory / "jobs").glob("**/result.json"))
    trials = []
    for p in outputs:
        data = read_json(p)
        if "trial_name" in data:
            trials.append({"result": evidence_ref(p, root), "agent": data.get("agent_info"),
                           "native_rewards": (data.get("verifier_result") or {}).get("rewards"),
                           "exception_info": data.get("exception_info"), "trial_id": data.get("id")})
    manifest = {"task_key": row["task_key"], "run_id": run_id, "kind": label, "backend": backend,
                "runtime": runtime, "backend_binding": binding_identity(root, row, backend),
                "native_execution": True, "qwen_smoke": False, "exit_code": result.returncode,
                "completed": result.returncode == 0 and len(trials) == len(cfg["agents"]) * cfg["n_attempts"],
                "case_file_sha256": file_hash(case_file) if case_file else None,
                "original_task_hash": row["adapted_task_hash"], "validation_copy_hash": tree_hash(copied),
                "case": case, "trials": trials,
                "evidence": [evidence_ref(config_path, root), evidence_ref(directory / "harbor.log", root)] + [t["result"] for t in trials],
                "review_needed": "Raw outcomes do not certify solvability, semantics, or stability. Review outputs, proof and logs."}
    write_json(directory / "execution.json", manifest)
    return manifest


def summarize(root, rows, records):
    keys = {r["task_key"] for r in rows}
    if {r["task_key"] for r in records} != keys or len(records) != len(keys):
        raise ValueError("Validation ledger must cover every candidate exactly once")
    totals = Counter(r["native"]["conclusion"] for r in records)
    audit = Counter(r["audit"]["conclusion"] for r in records)
    checks = Counter(c["status"] for r in records for c in r["native"]["checks"].values())
    audit_checks = Counter(c["status"] for r in records for c in r["audit"]["checks"].values())
    summary = {"candidate_count": len(records), "native": {k: totals[k] for k in ("validated", "weak", "broken", "pending")},
               "audit": {k: audit[k] for k in ("validated", "weak", "broken", "pending")},
               "checks": dict(checks), "native_check_counts": dict(checks), "audit_check_counts": dict(audit_checks),
               "executed_benchmark_checks": sum(checks[s] for s in ("pass", "fail", "gap")),
               "unexecuted_benchmark_checks": checks["pending"],
               "validation_complete": not totals["pending"] and not checks["pending"],
               "sampling_eligible": sum(r["sampling_eligible"] for r in records),
               "missing_real_witness": sum(r["witness_kind"] != "built_from_inputs" for r in records),
               "qwen_smoke_executed": False}
    write_jsonl(Path(root) / "validation/ledger.jsonl", records)
    write_json(Path(root) / "validation/summary.json", summary)
    lines = ["# Validation status", "", "Generated by `scripts/validate.py`; fixture regressions are not benchmark runs.", "",
             f"Candidates: {len(records)}. Native: {dict(totals)}. Audit: {dict(audit)}.",
             f"Checks: {dict(checks)}. Real build witnesses missing: {summary['missing_real_witness']}.",
             f"Validation-rule eligible: {summary['sampling_eligible']}. Full validation complete: {summary['validation_complete']}.", "",
             "This is verifier-protocol eligibility only. Frozen split, trial environment and trace eligibility are checked separately by report; see delivery-status.json for the usable task pool.", "",
             "| Task | Native | Audit | Witness | Pending checks | Record |", "|---|---|---|---|---:|---|"]
    for r in records:
        row = next(x for x in rows if x["task_key"] == r["task_key"])
        pending = sum(c["status"] == "pending" for c in r["native"]["checks"].values())
        lines.append(f"| {r['task_key']} | {r['native']['conclusion']} | {r['audit']['conclusion']} | {r['witness_kind']} | {pending} | [record](../{row['validation_ref']}) |")
    write_text(Path(root) / "docs/validation-status.md", "\n".join(lines) + "\n")
    return summary


def validate(root, *, selected=None, execute=False, case_file=None, resume=False, backend="docker"):
    root = Path(root).resolve()
    rows = load_catalog(root)
    selected = set(selected or [r["task_key"] for r in rows])
    if selected - {r["task_key"] for r in rows}:
        raise ValueError("Unknown selected task")
    risks = read_json(root / "validation/risk-register.json")["risks"]
    records, errors = [], []
    for row in rows:
        directory = task_dir(root, row)
        contract_path = directory / "contract.yaml"
        if not contract_path.exists():
            write_json(contract_path, initial_contract(row, risks))
        record_path = directory / "record.json"
        previous = read_json(record_path) if record_path.exists() else None
        record_backend = backend if row["task_key"] in selected else (previous or {}).get("identity", {}).get("backend", "docker")
        execution = None
        if execute and row["task_key"] in selected:
            try:
                previous_run = (previous or {}).get("execution") or {}
                same_case = previous_run.get("case_file_sha256") == (file_hash(case_file) if case_file else None)
                if resume and previous and reusable(previous, identity(root, row, contract_path, record_backend), root) and previous_run.get("completed") and same_case:
                    execution = previous["execution"]
                else:
                    execution = run_native(root, row, case_file=case_file, backend=record_backend)
            except (ValueError, OSError, RuntimeError, ImportError) as exc:
                errors.append({"task_key": row["task_key"], "reason": str(exc)})
                execution = {"native_execution": False, "error": str(exc), "backend": record_backend, "evidence": []}
        elif record_path.exists():
            previous = read_json(record_path)
            if reusable(previous, identity(root, row, contract_path, record_backend), root):
                execution = previous.get("execution")
        record = build_record(root, row, contract_path, execution, backend=record_backend)
        if record_path.exists() and read_json(record_path) != record:
            old = read_json(record_path)
            write_json(directory / "history" / f"{digest(old)}.json", old)
        write_json(record_path, record)
        records.append(record)
    summary = summarize(root, rows, records)
    return {"summary": summary, "execution_errors": errors}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--task", action="append")
    p.add_argument("--execute", action="store_true", help="Run native oracle twice and nop twice using Harbor")
    p.add_argument("--case", type=Path, help="Reviewed case JSON; requires exactly one --task")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--backend", choices=BACKENDS, default="docker")
    p.add_argument("--show-review-inputs", action="store_true", help="Print exact review binding for one existing task contract; does not certify it")
    args = p.parse_args(argv)
    if args.show_review_inputs:
        if not args.task or len(args.task) != 1 or args.execute:
            p.error("--show-review-inputs requires exactly one --task and no --execute")
        row = next((r for r in load_catalog(args.root) if r["task_key"] == args.task[0]), None)
        if row is None:
            p.error("Unknown task")
        print(json.dumps(review_inputs(identity(args.root, row, task_dir(args.root, row) / "contract.yaml", args.backend)), indent=2))
        return 0
    if args.case and (not args.task or len(args.task) != 1 or not args.execute):
        p.error("--case requires --execute and exactly one --task")
    result = validate(args.root, selected=args.task, execute=args.execute, case_file=args.case, resume=args.resume, backend=args.backend)
    print(json.dumps(result, indent=2))
    return 1 if result["execution_errors"] else 0
