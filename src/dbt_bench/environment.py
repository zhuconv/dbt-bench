"""Assess evidence from both real contexts without equating successful imports with compatibility."""

from .io import evidence_ok


def assess_contexts(agent, verifier, bridge, *, root):
    problems, differences = [], []
    for name, probe in (("agent", agent), ("verifier", verifier)):
        if not probe:
            return {"status": "pending", "reason": f"Missing {name} context probe"}
        if probe.get("duckdb_error") or not probe.get("duckdb_module", {}).get("origin"):
            problems.append(f"{name}: active Python cannot import DuckDB")
        dbt = probe.get("dbt", {})
        if not dbt.get("path") or (dbt.get("version") or {}).get("returncode") != 0:
            problems.append(f"{name}: dbt executable unavailable or fails")
        for database in probe.get("databases", []):
            if database.get("error") or not database.get("readable"):
                problems.append(f"{name}: database not readable: {database.get('path')}")
    for key in ("python", "duckdb_version", "dbt", "environment"):
        if agent.get(key) != verifier.get(key):
            differences.append(key)
    if problems:
        return {"status": "fail", "problems": problems, "differences": differences}
    if not bridge or not evidence_ok(bridge.get("evidence"), root):
        return {"status": "pending", "reason": "Need verifier read of agent-created DuckDB/output in native transfer mode", "differences": differences}
    if (bridge.get("producer_sha256") != bridge.get("consumer_sha256") or not bridge.get("producer_sha256")
            or bridge.get("rows_match") is not True or bridge.get("native_transfer_mode") not in {"shared", "separate"}):
        return {"status": "fail", "reason": "Cross-context output transfer/read failed", "differences": differences}
    if differences and not bridge.get("difference_explanation"):
        return {"status": "pending", "reason": "Explain context differences", "differences": differences}
    return {"status": "pass", "differences": differences, "scope": "Declared database read/transfer only; setup and agent visibility require separate evidence"}
