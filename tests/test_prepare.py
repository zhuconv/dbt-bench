from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import io

from dbt_bench.io import evidence_ref, file_hash, read_json, tree_hash, write_json, write_jsonl
from dbt_bench.prepare import apply_patch, checkout, hydrate_hf_blobs, prepare_tasks, validate_layout
from tests.helpers import fixture_root, native_task


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True).stdout.strip()


class PrepareTests(unittest.TestCase):
    def test_hf_http_success_cannot_replace_git_blob_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin = Path(tmp)
            native_task(origin / "tasks/fixture")
            git(origin, "init")
            git(origin, "add", ".")
            git(origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-m", "fixture")
            revision = git(origin, "rev-parse", "HEAD")
            def wrong_response(*args, **kwargs):
                response = io.BytesIO(b"Wrong bytes from a successful HTTP response")
                response.headers = {"x-repo-commit": revision}
                return response
            with patch("dbt_bench.prepare.urllib.request.urlopen", side_effect=wrong_response):
                with self.assertRaisesRegex(ValueError, "locked Git blob"):
                    hydrate_hf_blobs({"url": "https://huggingface.co/datasets/fixture/fixture", "revision": revision},
                                     origin, ["tasks/fixture"])

    def test_sparse_fetch_materializes_requested_tasks_and_preserves_previous_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / "upstream"
            native_task(origin / "tasks/first")
            native_task(origin / "tasks/second")
            (origin / "unrelated").mkdir()
            (origin / "unrelated/large.txt").write_text("irrelevant benchmark data")
            git(origin, "init")
            git(origin, "config", "uploadpack.allowFilter", "true")
            git(origin, "add", ".")
            git(origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-m", "fixture")
            revision = git(origin, "rev-parse", "HEAD")
            unrelated_blob = git(origin, "rev-parse", "HEAD:unrelated/large.txt")
            spec = {"revision": revision, "url": origin.as_uri()}
            destination = root / "checkout"
            checkout(spec, destination, True, task_paths=["tasks/first"])
            self.assertTrue((destination / "tasks/first/task.toml").is_file())
            self.assertFalse((destination / "tasks/second").exists())
            self.assertFalse((destination / "unrelated/large.txt").exists())
            self.assertIn("?" + unrelated_blob, git(destination, "rev-list", "--objects", "--all", "--missing=print"))
            checkout(spec, destination, True, task_paths=["tasks/second"])
            self.assertTrue((destination / "tasks/first/task.toml").is_file())
            self.assertTrue((destination / "tasks/second/task.toml").is_file())
            self.assertEqual(git(destination, "rev-parse", "HEAD"), revision)
            self.assertEqual(git(destination, "status", "--porcelain"), "")

    def test_repeat_prepare_preserves_native_bytes_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bench"
            row = fixture_root(root)
            origin = Path(tmp) / "upstream"
            original = native_task(origin / "tasks/fixture")
            git(origin, "init")
            git(origin, "add", ".")
            git(origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-m", "fixture")
            revision = git(origin, "rev-parse", "HEAD")
            lock = read_json(root / "sources.lock.yaml")
            lock["sources"]["ade"].update(revision=revision, task_subdir="tasks")
            write_json(root / "sources.lock.yaml", lock)
            result = prepare_tasks(root, source_roots={"ade": str(origin)})
            self.assertEqual(result["prepared"], [row["task_key"]])
            prepared = root / row["run_path"]
            self.assertEqual(tree_hash(prepared), tree_hash(original))
            before = (root / "catalog/tasks.jsonl").read_bytes()
            self.assertFalse(prepare_tasks(root, source_roots={"ade": str(origin)})["failures"])
            self.assertEqual(before, (root / "catalog/tasks.jsonl").read_bytes())
            (prepared / "instruction.md").write_text("changed")
            self.assertTrue(prepare_tasks(root, source_roots={"ade": str(origin)})["failures"])
            self.assertEqual((prepared / "instruction.md").read_text(), "changed")

    def test_wrong_revision_and_dirty_upstream_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            native_task(root / "task")
            git(root, "init")
            git(root, "add", ".")
            git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-m", "fixture")
            revision = git(root, "rev-parse", "HEAD")
            with self.assertRaises(ValueError):
                checkout({"revision": "0" * 40}, root, False)
            (root / "task/instruction.md").write_text("modified")
            with self.assertRaises(ValueError):
                checkout({"revision": revision}, root, False)

    def test_lfs_pointer_and_escaping_symlink_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = native_task(Path(tmp) / "task")
            (root / "data").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")
            with self.assertRaises(ValueError):
                validate_layout(root)
            (root / "data").unlink()
            (root / "outside").symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                tree_hash(root)

    def test_patch_exact_context_and_regression_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = native_task(root / "task")
            patch = root / "patch"
            patch.mkdir()
            (patch / "new.txt").write_text("new instruction")
            (patch / "regression.txt").write_text("fixture regression evidence")
            original = tree_hash(task)
            metadata = {"id": "fixture", "type": "task_variant", "new_version": "fixture-v2", "upstream_task_hash": original,
                        "reason": "test exact patch matching", "regression_evidence": [evidence_ref(patch / "regression.txt", patch)],
                        "files": [{"path": "instruction.md", "before_sha256": file_hash(task / "instruction.md"), "after_sha256": file_hash(patch / "new.txt"), "replacement": "new.txt"}]}
            write_json(patch / "metadata.json", metadata)
            apply_patch(task, patch, original)
            self.assertEqual((task / "instruction.md").read_text(), "new instruction")
            with self.assertRaises(ValueError):
                apply_patch(task, patch, original)
