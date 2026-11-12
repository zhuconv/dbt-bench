from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

from dbt_bench.io import digest, evidence_ref, file_hash, read_json, tree_hash, write_json, write_jsonl
from dbt_bench.validation import (CHECKS, adjudicate, build_record, identity, initial_contract,
                                 reusable, review_inputs, task_dir, validate, _wrap_case)
from tests.helpers import fixture_root, native_task


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.row = fixture_root(self.root)
        self.contract_path = task_dir(self.root, self.row) / "contract.yaml"
        self.contract = initial_contract(self.row, [])
        write_json(self.contract_path, self.contract)

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_assets_never_validated(self):
        record = build_record(self.root, self.row, self.contract_path)
        self.assertEqual(record["native"]["conclusion"], "pending")
        self.assertFalse(record["sampling_eligible"])
        self.assertTrue(record["blockers"])

    def test_resume_requires_exact_hash_and_present_evidence(self):
        record = build_record(self.root, self.row, self.contract_path)
        ident = record["identity"]
        self.assertTrue(reusable(record, ident, self.root))
        changed = {**ident, "task": "different"}
        self.assertFalse(reusable(record, changed, self.root))
        self.contract_path.write_text(self.contract_path.read_text() + "\n")
        self.assertFalse(reusable(record, ident, self.root))

    def test_trace_report_edits_do_not_change_verifier_identity(self):
        original=identity(self.root,self.row,self.contract_path)
        for name in ['trace.py','report.py']:
            (self.root/'src/dbt_bench'/name).write_text('changed read-only report implementation')
        self.assertEqual(identity(self.root,self.row,self.contract_path),original)
        (self.root/'src/dbt_bench/comparison.py').write_text('changed verifier comparison')
        self.assertNotEqual(identity(self.root,self.row,self.contract_path),original)

    def complete_contract(self, witness="built_from_inputs"):
        evidence = self.root / "proof.json"
        write_json(evidence, {"scope": "synthetic fixture", "business_error_proved_independently": True})
        ref = evidence_ref(evidence, self.root)
        checks = {c: {"status": "pass", "reason": "Synthetic evidence for policy test only", "evidence": [ref]} for c in CHECKS}
        contract = {**self.contract, "review_status": "reviewed", "reference_kind": "full_gold",
                    "witness_kind": witness, "requirements": [{"id": "R1", "critical": True, "coverage": {
                        "native": {"status": "covered", "evidence": [ref]}}}]}
        return contract, checks, ref

    def test_copy_gold_cannot_certify_solvability(self):
        contract, checks, _ = self.complete_contract(witness="copy_gold")
        self.assertEqual(adjudicate(contract, checks, identity_complete=True, root=self.root)[0], "pending")

    def test_positive_control_can_be_validated_with_complete_protocol(self):
        contract, checks, _ = self.complete_contract()
        self.assertEqual(adjudicate(contract, checks, identity_complete=True, root=self.root)[0], "validated")
        checks["negatives"]["status"] = "pending"
        self.assertEqual(adjudicate(contract, checks, identity_complete=True, root=self.root)[0], "pending")

    def test_weak_requires_independently_proved_business_error(self):
        contract, checks, ref = self.complete_contract()
        coverage = {"status": "missed_wrong", "case_validity": "business_error", "checker_evidence": ref}
        contract["requirements"][0]["coverage"]["native"] = coverage
        self.assertEqual(adjudicate(contract, checks, identity_complete=True, root=self.root)[0], "pending")
        coverage["independent_proof"] = ref
        self.assertEqual(adjudicate(contract, checks, identity_complete=True, root=self.root)[0], "weak")
        coverage["case_validity"] = "sql_execution_failure"
        self.assertEqual(adjudicate(contract, checks, identity_complete=True, root=self.root)[0], "pending")

    def test_changed_task_invalidates_review_even_when_logs_remain(self):
        path = native_task(self.root / self.row["run_path"])
        self.row["adapted_task_hash"] = tree_hash(path)
        contract, checks, ref = self.complete_contract()
        contract["checks"] = checks
        contract["diagnoses"] = [{"kind": "broken", "reason": "synthetic fixture diagnosis", "evidence": [ref]}]
        contract["reviewed_inputs"] = review_inputs(identity(self.root, self.row, self.contract_path))
        write_json(self.contract_path, contract)
        record = build_record(self.root, self.row, self.contract_path)
        self.assertEqual(record["native"]["conclusion"], "broken")
        (path / "instruction.md").write_text("changed task")
        new = build_record(self.root, self.row, self.contract_path)
        self.assertEqual(new["native"]["conclusion"], "pending")
        self.assertFalse(new["review_current"])

    def test_full_ledger_has_one_record_per_candidate(self):
        result = validate(self.root)
        self.assertEqual(result["summary"]["candidate_count"], 1)
        self.assertEqual(result["summary"]["checks"]["pending"], len(CHECKS))
        before = (self.root / "validation/ledger.jsonl").read_bytes()
        validate(self.root)
        self.assertEqual(before, (self.root / "validation/ledger.jsonl").read_bytes())

    def test_no_false_weak_from_historical_label(self):
        self.contract["legacy_excluded"] = True
        self.contract["diagnoses"] = [{"kind": "broken", "reason": "Old label", "evidence": []}]
        write_json(self.contract_path, self.contract)
        self.assertEqual(build_record(self.root, self.row, self.contract_path)["native"]["conclusion"], "pending")

    def test_case_proof_gate_runs_before_unchanged_native_checker(self):
        task = native_task(self.root / "isolated-task")
        case_dir = self.root / "case"
        case_dir.mkdir()
        for name in ("submit.sh", "proof.sh"):
            (case_dir / name).write_text("#!/bin/sh\nexit 0\n")
        case = {"kind": "negative", "reviewed": True, "requirement_ids": ["R1"],
                "independent_derivation": "fixture derivation", "submission_script": "submit.sh",
                "proof_script": "proof.sh", "run_native_reference": True}
        original = (task / "tests/test.sh").read_bytes()
        _wrap_case(task, case_dir, case)
        self.assertEqual(original, (task / "tests/native-test.sh").read_bytes())
        proof_path = self.root / "proof.json"
        # Execute the actual generated gate against isolated fixture paths; no benchmark run.
        gate = (task / "tests/proof_gate.py").read_text().replace("/logs/agent/case-proof.json", str(proof_path))
        artifact = self.root / "data"
        artifact.write_text("fixture data")
        ref = {"path": str(artifact), "sha256": file_hash(artifact)}
        proof = {"validity": "business_error", "derivation": "fixture independent proof", "submission_artifacts": [ref], "input_artifacts": [ref]}
        write_json(proof_path, proof)
        result = subprocess.run([sys.executable, "-c", gate], capture_output=True)
        self.assertEqual(result.returncode, 0)
        artifact.write_text("tampered after proof")
        self.assertNotEqual(subprocess.run([sys.executable, "-c", gate], capture_output=True).returncode, 0)
        proof["validity"] = "sql_execution_failure"
        write_json(proof_path, proof)
        self.assertNotEqual(subprocess.run([sys.executable, "-c", gate], capture_output=True).returncode, 0)
