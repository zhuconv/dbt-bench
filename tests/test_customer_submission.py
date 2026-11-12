"""Submission replay must not retain deleted models or accept changed bytes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('customer_replay',Path(__file__).resolve().parents[1]/'scripts/check_customer_submission.py')
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


class CustomerReplayTests(unittest.TestCase):
    def run_probe(self, source, root, receipt):
        source = source.replace("Path('/app')",'Path('+repr(str(root))+')')
        source = source.replace("Path('/tmp/submission-receipt.json')",'Path('+repr(str(receipt))+')')
        return subprocess.run([sys.executable,'-c',source],capture_output=True,text=True)

    def fixture(self, directory):
        root = directory/'app'
        (root/'models/staging').mkdir(parents=True)
        (root/'models/customer.sql').write_text('select 1 as customer_id')
        receipt = directory/'receipt.json'
        receipt.write_text(json.dumps({'files':[{'path':'submission/workspace/models/customer.sql',
            'sha256':hashlib.sha256(b'select 1 as customer_id').hexdigest(),'executable':False}]}))
        return root, receipt

    def test_deleted_model_is_removed_without_deleting_overlay_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, receipt = self.fixture(Path(tmp))
            deleted = root/'models/staging/deleted.sql'
            deleted.write_text('select hidden_old_value from old_table')
            self.assertNotEqual(self.run_probe(replay.CHECK,root,receipt).returncode,0)
            result = self.run_probe(replay.PRUNE,root,receipt)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse(deleted.exists())
            self.assertTrue((root/'models/staging').is_dir())
            self.assertEqual(self.run_probe(replay.CHECK,root,receipt).returncode,0)

    def test_same_filename_with_wrong_content_does_not_pass_restore_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, receipt = self.fixture(Path(tmp))
            (root/'models/customer.sql').write_text('select 2 as customer_id')
            self.assertEqual(self.run_probe(replay.PRUNE,root,receipt).returncode,0)
            result = self.run_probe(replay.CHECK,root,receipt)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Fresh workspace differs',result.stderr)

    def test_symlink_to_external_input_is_not_accepted_as_captured_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            root, receipt = self.fixture(directory)
            external = directory/'outside.sql'
            external.write_text('select 1 as customer_id')
            (root/'models/customer.sql').unlink()
            (root/'models/customer.sql').symlink_to(external)
            self.assertNotEqual(self.run_probe(replay.CHECK,root,receipt).returncode,0)
