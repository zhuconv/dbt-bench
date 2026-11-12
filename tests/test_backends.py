from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from dbt_bench.backends import (_set_image, binding_identity, configure_backend,
                               find_apptainer, harbor_process_env, register_sif, resolve_task)
from dbt_bench.backends import require_runtime
from dbt_bench.io import evidence_ref, file_hash, read_json, tree_hash, write_json, write_jsonl
from dbt_bench.prepare import emit_config
from dbt_bench.validation import identity, initial_contract, reusable, build_record, task_dir
from tests.helpers import fixture_root, native_task, qwen_bundle_fixture, REPO


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.row = fixture_root(self.root)
        self.task = native_task(self.root / self.row["run_path"])
        self.row["adapted_task_hash"] = tree_hash(self.task)
        write_jsonl(self.root / "catalog/tasks.jsonl", [self.row])

    def tearDown(self):
        self.temp.cleanup()

    def test_module_install_found_when_not_on_path(self):
        binary = self.root / "module/bin/apptainer"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        with patch("dbt_bench.backends.shutil.which", return_value=None), patch("dbt_bench.backends.TACC_APPTAINER", binary):
            self.assertEqual(find_apptainer(), binary)
            with self.assertRaises(RuntimeError):
                harbor_process_env("apptainer")
            (binary.parent / "singularity").symlink_to(binary)
            self.assertTrue(harbor_process_env("apptainer")["PATH"].startswith(str(binary.parent)))

    def test_native_backend_selection_preserves_budget(self):
        cfg = {"environment": {"type": "docker", "env": {"DB_TYPE": "duckdb"}}, "n_attempts": 2, "timeout_multiplier": 1.0}
        configure_backend(cfg, "apptainer")
        self.assertEqual(cfg["environment"]["type"], "singularity")
        self.assertEqual(cfg["n_attempts"], 2)
        self.assertEqual(cfg["timeout_multiplier"], 1.0)
        self.assertEqual(cfg["environment"]["env"], {"DB_TYPE": "duckdb"})

    def test_unpinned_runtime_rejected(self):
        with patch("dbt_bench.backends.runtime_info", return_value={"available": True, "version": "apptainer version 1.3.3"}):
            with self.assertRaises(RuntimeError):
                require_runtime("apptainer")

    def test_image_edit_preserves_all_other_native_toml(self):
        original = 'version = "1.0"\n[environment]\nmemory_mb=4096\ndocker_image="native:tag"\n[agent]\ntimeout_sec=120\n[verifier]\ntimeout_sec=30\n'
        changed = tomllib.loads(_set_image(original, '/images/task with space.sif'))
        expected = tomllib.loads(original)
        expected["environment"]["docker_image"] = '/images/task with space.sif'
        self.assertEqual(changed, expected)
        self.assertEqual(tomllib.loads(_set_image('[environment]', '/a.sif'))["environment"]["docker_image"], '/a.sif')

    def provenance(self):
        image = self.root / "fixture.sif"
        image.write_bytes(b"test-double-image-not-a-real-sif")
        evidence = self.root / "build.log"
        evidence.write_text("Synthetic binding test evidence; not a real image or benchmark")
        proof = {"native_task_hash": self.row["adapted_task_hash"],
                 "dockerfile_sha256": file_hash(self.task / "environment/Dockerfile"),
                 "image_sha256": file_hash(image), "complete_native_build": True,
                 "architecture": platform.machine(), "build_kind": "native_oci_image",
                 "build_evidence": [evidence_ref(evidence, self.root)],
                 "agent_visibility_evidence": evidence_ref(evidence, self.root)}
        path = self.root / "provenance.json"
        write_json(path, proof)
        return image, path, proof

    def register(self, image, path):
        with patch("dbt_bench.backends.require_runtime", return_value={"binary": "fixture-apptainer", "available": True}), patch(
                "dbt_bench.backends.subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{"synthetic":true}', '')):
            return register_sif(self.root, self.row, image, path)

    def test_binding_keeps_native_snapshot_and_detects_changed_sif(self):
        image, path, _ = self.provenance()
        before = tree_hash(self.task)
        manifest = self.register(image, path)
        staged = resolve_task(self.root, self.row, "apptainer")
        self.assertEqual(tree_hash(self.task), before)
        self.assertEqual((staged / "tests/test.sh").read_bytes(), (self.task / "tests/test.sh").read_bytes())
        self.assertEqual(tomllib.loads((staged / "task.toml").read_text())["environment"]["docker_image"], str(image))
        self.assertEqual(self.register(image, path), manifest)
        image.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            resolve_task(self.root, self.row, "apptainer")

    def test_base_image_wrong_arch_and_missing_visibility_rejected(self):
        image, path, proof = self.provenance()
        for change in ({"complete_native_build": False}, {"architecture": "wrong-architecture"}, {"agent_visibility_evidence": None}):
            write_json(path, {**proof, **change})
            with self.assertRaises(ValueError):
                self.register(image, path)

    def test_backend_switch_invalidates_validation(self):
        contract = task_dir(self.root, self.row) / "contract.yaml"
        write_json(contract, initial_contract(self.row, []))
        docker = build_record(self.root, self.row, contract)
        apptainer = identity(self.root, self.row, contract, "apptainer")
        self.assertFalse(reusable(docker, apptainer, self.root))
        self.assertEqual(apptainer["backend_binding"]["status"], "missing_sif_binding")

    def test_config_uses_bound_sif_task_and_distinct_experiment(self):
        qwen_bundle_fixture(self.root)
        image, path, _ = self.provenance()
        self.register(image, path)
        shutil.copy2(REPO / "configs/ade.yaml", self.root / "configs/ade.yaml")
        write_json(self.root / "catalog/prepared" / f"{self.row['task_key']}.json", {"synthetic": True})
        docker = emit_config(self.root, "ade", "openai/fixture", "http://fixture.invalid", "fixture", check=False)
        apptainer = emit_config(self.root, "ade", "openai/fixture", "http://fixture.invalid", "fixture", check=False, backend="apptainer")
        self.assertEqual(read_json(apptainer)["environment"]["type"], "singularity")
        self.assertNotEqual(read_json(docker)["tasks"], read_json(apptainer)["tasks"])
        self.assertNotEqual(read_json(docker.with_suffix('.manifest.json'))["experiment_id"], read_json(apptainer.with_suffix('.manifest.json'))["experiment_id"])
