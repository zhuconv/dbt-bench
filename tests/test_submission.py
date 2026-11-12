import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from dbt_bench.io import write_json
from dbt_bench.submission import inspect_submission

ROOT = Path(__file__).resolve().parents[1]


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trial = Path(self.tmp.name)
        self.folder = self.trial / 'artifacts/submission/project'
        self.folder.mkdir(parents=True)
        (self.folder / 'dbt_project.yml').write_text('name: fixture')
        self.spec = {'artifacts': [{'source': '/app/project', 'destination': 'submission/project', 'exclude': []}],
                     'required_files': ['submission/project/dbt_project.yml']}
        self.entry = {**self.spec['artifacts'][0], 'service': None, 'status': 'ok', 'type': 'directory'}
        self.entry['destination'] = 'artifacts/submission/project'
        write_json(self.trial / 'artifacts/manifest.json', [self.entry])

    def test_snapshot_identity_changes_with_submission_bytes(self):
        before = inspect_submission(self.trial, self.spec)
        self.assertTrue(before['complete'])
        (self.folder / 'dbt_project.yml').write_text('name: altered')
        after = inspect_submission(self.trial, self.spec)
        self.assertTrue(after['complete'])
        self.assertNotEqual(before['snapshot_hash'], after['snapshot_hash'])

    def test_failed_missing_duplicate_or_excluded_artifacts_are_not_complete(self):
        for entries in ([], [self.entry, self.entry], [{**self.entry, 'status': 'failed'}],
                        [{**self.entry, 'exclude': ['*.sql']}], [{**self.entry, 'destination': 'wrong'}]):
            write_json(self.trial / 'artifacts/manifest.json', entries)
            self.assertFalse(inspect_submission(self.trial, self.spec)['complete'])

    def test_missing_file_and_escaping_links_are_rejected(self):
        path = self.folder / 'dbt_project.yml'
        path.unlink()
        self.assertFalse(inspect_submission(self.trial, self.spec)['complete'])
        path.symlink_to('/etc/passwd')
        self.assertFalse(inspect_submission(self.trial, self.spec)['complete'])

    def test_actual_native_lifecycle_captures_before_verifier_mutation_and_cleanup(self):
        python = ROOT / 'runtime/.venv/bin/python'
        if not python.exists():
            self.skipTest('Pinned Harbor runtime not installed')
        code = '''
import asyncio,tempfile
from pathlib import Path
from types import SimpleNamespace
from harbor.trial.single_step import SingleStepTrial
from harbor.models.task.config import TaskConfig
async def test():
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);submission=root/'submission';captured=root/'captured'
  async def agent():submission.write_text('agent output')
  async def logs():pass
  async def capture(**kwargs):captured.write_bytes(submission.read_bytes())
  async def verifier():submission.write_text('verifier rewrite')
  async def stop():submission.unlink()
  trial=SimpleNamespace(task=SimpleNamespace(config=TaskConfig()),_run_agent=agent,
   _upload_agent_logs=logs,_collect_artifacts=capture,_run_verifier=verifier,_stop_agent_environment=stop)
  await SingleStepTrial._run(trial)
  assert captured.read_text()=='agent output' and not submission.exists()
asyncio.run(test())
'''
        result = subprocess.run([str(python), '-c', code], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
