from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dbt_bench.io import digest, evidence_ref, file_hash, read_json, write_json
from dbt_bench.report import eligibility, native_success, reconcile, report, verify_submission, reviewed_success_rule, split_provenance
from dbt_bench.submission import inspect_submission
from dbt_bench.validation import validate
from tests.helpers import fixture_root


class ReportingTests(unittest.TestCase):
    def test_reselection_preserves_generation_identity_and_train_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row = fixture_root(root)
            row.update(split='test', exposure_history=[{'version':'v3','split':'train','training_use':'unknown'}])
            generation = {'catalog_hash':'original-catalog', 'split_hash':'original-split'}
            original = deepcopy(generation)
            provenance = split_provenance(root,row,generation)
            self.assertEqual(provenance['generation'], original)
            self.assertEqual(generation, original)
            self.assertEqual(provenance['selection']['split'], 'test')
            self.assertNotEqual(provenance['selection']['split_hash'], original['split_hash'])
            self.assertEqual(provenance['historical_exposure'][0]['split'], 'train')

    def setUp(self):
        self.trial = {"split": "train", "identity_verified": True, "environment_valid": True,
                      "native_reward_valid": True, "native_success": True, "validation_current": True,
                      "submission_verified": True,
                      "trace_quality": {"schema_valid": True, "content_fidelity": "complete", "model_context_capture": "unknown"}}
        self.validation = {"native": {"conclusion": "validated"}, "audit": {"conclusion": "pending"}}

    def eligible(self):
        return eligibility(self.trial, self.validation, split_status="reviewed_frozen", root=Path.cwd())

    def test_candidate_not_automatically_sft_ready(self):
        self.assertTrue(self.eligible()["candidate"])
        self.assertFalse(self.eligible()["strict_sft_ready"])
        self.trial["trace_quality"]["model_context_capture"] = "complete"
        self.assertTrue(self.eligible()["strict_sft_ready"])

    def test_test_split_and_pending_validation_excluded(self):
        self.trial["split"] = "test"
        self.assertFalse(self.eligible()["candidate"])
        self.trial["split"] = "train"
        self.validation["native"]["conclusion"] = "pending"
        self.assertFalse(self.eligible()["candidate"])

    def test_weak_native_requires_separate_validated_audit(self):
        self.validation["native"]["conclusion"] = "weak"
        self.trial.update(audit_success=True, same_submission_verified=True)
        self.assertFalse(self.eligible()["candidate"])
        self.validation["audit"]["conclusion"] = "validated"
        self.assertTrue(self.eligible()["candidate"])
        self.trial["same_submission_verified"] = False
        self.assertFalse(self.eligible()["candidate"])

    def test_stale_or_invalid_or_incomplete_excluded(self):
        for key in ("identity_verified", "environment_valid", "native_reward_valid", "native_success", "validation_current", "submission_verified"):
            original = self.trial[key]
            self.trial[key] = None
            self.assertFalse(self.eligible()["candidate"], key)
            self.trial[key] = original
        self.trial["trace_quality"]["content_fidelity"] = "incomplete"
        self.assertFalse(self.eligible()["candidate"])

    def test_positive_number_not_generic_success(self):
        rule = {"verification": "reviewed", "metric": "accuracy", "equals": 1}
        self.assertFalse(native_success({"accuracy": 0.2, "other": 1}, rule))
        self.assertIsNone(native_success({"reward": 1}, rule))
        self.assertIsNone(native_success({"accuracy": 1}, {**rule, "verification": "pending"}))

    def test_reviewed_task_success_does_not_promote_its_unreviewed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);row={'source':'ade','upstream_task_id':'customer'}
            path=root/'validation/ade/customer/contract.yaml'
            write_json(path,{'native_success':{'reviewed':True,'metric':'reward','equals':1.0}})
            validation={'review_current':True,'identity':{'contract':file_hash(path)}}
            source={'verification':'pending','metric':'reward','equals':1.0}
            resolved=reviewed_success_rule(root,row,validation,source,current=True)
            self.assertTrue(native_success({'reward':1.0},resolved['rule']))
            self.assertFalse(native_success({'reward':0.5},resolved['rule']))
            self.assertIsNone(native_success({'reward':1.0},source))
            self.assertEqual(resolved['scope'],'task_contract')

    def test_changed_or_stale_contract_cannot_supply_success_definition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);row={'source':'ade','upstream_task_id':'customer'}
            path=root/'validation/ade/customer/contract.yaml'
            write_json(path,{'native_success':{'reviewed':True,'metric':'reward','equals':1.0}})
            validation={'review_current':True,'identity':{'contract':file_hash(path)}}
            source={'verification':'pending','metric':'reward','equals':1.0}
            resolved=reviewed_success_rule(root,row,validation,source,current=False)
            self.assertIsNone(native_success({'reward':1.0},resolved['rule']))
            write_json(path,{'native_success':{'reviewed':True,'metric':'reward','equals':0.5}})
            resolved=reviewed_success_rule(root,row,validation,source,current=True)
            self.assertIsNone(native_success({'reward':0.5},resolved['rule']))

    def test_saved_receipt_cannot_hide_changed_submission_or_missing_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trial = root / 'trial'
            project = trial / 'artifacts/submission/project'
            project.mkdir(parents=True)
            model = project / 'answer.sql';model.write_text('select 1')
            entry = {'source': '/app/project', 'destination': 'submission/project', 'exclude': []}
            spec = {'task_key': 'fixture', 'artifacts': [entry], 'required_files': ['submission/project/answer.sql']}
            write_json(trial / 'artifacts/manifest.json', [{**entry, 'destination':'artifacts/submission/project',
                       'status':'ok', 'type':'directory', 'service':None}])
            write_json(root / 'spec.json', spec)
            write_json(root / 'receipt.json', inspect_submission(trial, spec))
            binding = {'task_key':'fixture', 'submission':evidence_ref(root / 'receipt.json', root)}
            identity = {'submission_capture': {'spec':evidence_ref(root / 'spec.json', root)}}
            config = {'artifacts':[entry]}
            self.assertTrue(verify_submission(root, trial, config, binding, identity, True)[0])
            self.assertFalse(verify_submission(root, trial, {}, binding, identity, True)[0])
            self.assertFalse(verify_submission(root, trial, config, binding, identity, False)[0])
            model.write_text('select wrong_answer')
            self.assertFalse(verify_submission(root, trial, config, binding, identity, True)[0])
            # Even a newly generated receipt cannot replace the originally bound receipt hash.
            write_json(root / 'receipt.json', inspect_submission(trial, spec))
            self.assertFalse(verify_submission(root, trial, config, binding, identity, True)[0])

    def test_missing_unknown_duplicates_and_retries(self):
        planned = [{"task_key": "a", "attempt": 1}, {"task_key": "b", "attempt": 1}]
        trials = [{"trial_id": "one", "task_key": "a", "attempt": 1},
                  {"trial_id": "two", "task_key": "a", "attempt": 1, "retry_of": "one"}]
        result = reconcile(planned, trials)
        self.assertEqual(result["missing"], [{"task_key": "b", "attempt": 1}])
        self.assertEqual(result["infrastructure_retries"], 1)
        self.assertFalse(result["errors"])
        trials.append({"trial_id": "three", "task_key": "a", "attempt": 1})
        self.assertTrue(any(e["kind"] == "duplicate_attempt" for e in reconcile(planned, trials)["errors"]))
        trials.append({"trial_id": "four", "task_key": "unknown", "attempt": 1})
        self.assertTrue(any(e["kind"] == "unknown_task_attempt" for e in reconcile(planned, trials)["errors"]))
        with self.assertRaises(ValueError):
            reconcile(planned, [trials[0], trials[0]])

    def test_cycle_and_wrong_retry_parent(self):
        planned = [{"task_key": "a", "attempt": 1}]
        trials = [{"trial_id": "1", "task_key": "a", "attempt": 1, "retry_of": "2"},
                  {"trial_id": "2", "task_key": "a", "attempt": 1, "retry_of": "1"}]
        self.assertTrue(any(e["kind"] == "retry_cycle" for e in reconcile(planned, trials)["errors"]))

    def test_report_preserves_raw_error_and_null_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = fixture_root(root)
            validate(root)
            data = {"id": "trial-1", "trial_name": "raw", "task_name": "fixture",
                    "verifier_result": {"rewards": None}, "reward_valid": False,
                    "exception_info": {"exception_type": "AgentTimeoutError", "exception_message": "timeout"},
                    "config": {"job_id": "job-1"}}
            path = root / "jobs/j/raw/result.json"
            write_json(path, data)
            before = path.read_bytes()
            identity = {"tasks": {row["task_key"]: None}}
            manifest = {"identity": identity, "experiment_id": digest(identity), "planned": [{"task_key": row["task_key"], "attempt": 1}]}
            write_json(root / "experiment.json", manifest)
            summary = report(root, jobs=[root / "jobs"], experiment_path=root / "experiment.json")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(summary["candidate_count"], 0)
            from dbt_bench.io import read_jsonl
            trial = read_jsonl(root / "reports/latest/trials.jsonl")[0]
            self.assertIsNone(trial["audit_reward"])
            self.assertEqual(trial["native_exception_info"], data["exception_info"])
            self.assertIsNone(trial["cause_annotation"])
            self.assertTrue(summary["coverage"]["errors"])
