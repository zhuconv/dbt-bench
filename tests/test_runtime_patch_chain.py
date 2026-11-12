"""Patch upgrades must work from clean, intermediate and current installed states."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('runtime_patcher', ROOT / 'scripts/apply_runtime_patches.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def edit(before, after):
    return {'before_sha256': hashlib.sha256(before.encode()).hexdigest(),
            'after_sha256': hashlib.sha256(after.encode()).hexdigest(),
            'before_text': before, 'after_text': after}


class PatchChainTests(unittest.TestCase):
    def test_patch_does_not_mutate_hardlinked_cache_or_other_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache, target, peer = root/'cached.py',root/'installed.py',root/'peer.py'
            cache.write_text('upstream'); cache.chmod(0o640)
            os.link(cache,target); os.link(cache,peer)
            self.assertEqual(cache.stat().st_ino,target.stat().st_ino)
            patcher.replace_installed_file(target,'patched')
            self.assertEqual(target.read_text(),'patched')
            self.assertEqual(cache.read_text(),'upstream')
            self.assertEqual(peer.read_text(),'upstream')
            self.assertNotEqual(cache.stat().st_ino,target.stat().st_ino)
            self.assertEqual(target.stat().st_mode & 0o777,0o640)

    def test_missing_run_artifact_can_use_identical_committed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b'{"passed": true}\n'
            checksum = hashlib.sha256(content).hexdigest()
            mirror = root / 'validation/evidence/runtime-patch-assets' / checksum
            mirror.parent.mkdir(parents=True); mirror.write_bytes(content)
            resolved = patcher.resolve_regression({'path':'artifacts/run/result.json','sha256':checksum},root)
            self.assertEqual(resolved['path'],str(mirror.relative_to(root)))
            self.assertEqual(resolved['sha256'],checksum)

    def test_mirror_cannot_hide_changed_original_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b'original'
            checksum = hashlib.sha256(content).hexdigest()
            mirror = root / 'validation/evidence/runtime-patch-assets' / checksum
            mirror.parent.mkdir(parents=True); mirror.write_bytes(content)
            original = root / 'artifacts/result.json'
            original.parent.mkdir(); original.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'evidence changed'):
                patcher.resolve_regression({'path':'artifacts/result.json','sha256':checksum},root)

    def test_clean_and_intermediate_install_reach_same_final_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'adapter.py'
            edits = [edit('original\n', 'exec fix\n'), edit('exec fix\n', 'exec and lifecycle fixes\n')]
            for initial in ('original\n', 'exec fix\n'):
                target.write_text(initial)
                staged = patcher.plan_updates({target: edits})
                self.assertEqual(len(staged), 1)
                self.assertEqual(staged[0][2], 'exec and lifecycle fixes\n')
                self.assertEqual(target.read_text(), initial, 'Planning must not mutate the runtime')

    def test_final_chain_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'adapter.py'
            target.write_text('third')
            self.assertEqual(patcher.plan_updates({target: [edit('first','second'),edit('second','third')]}), [])

    def test_unknown_preimage_and_broken_chain_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'adapter.py'
            for initial, edits in [('local change', [edit('first','second')]),
                                   ('first', [edit('first','second'),edit('other','third')])]:
                target.write_text(initial)
                with self.assertRaises(ValueError):
                    patcher.plan_updates({target: edits})
                self.assertEqual(target.read_text(), initial)

    def test_multi_hunk_edit_checks_exact_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'adapter.py'
            target.write_text('alpha beta')
            entry = edit('alpha beta','ALPHA BETA')
            entry['replacements'] = [{'before_text':'alpha','after_text':'ALPHA'},
                                     {'before_text':'beta','after_text':'BETA'}]
            self.assertEqual(patcher.plan_updates({target:[entry]})[0][2], 'ALPHA BETA')
            entry['replacements'][1]['after_text'] = 'wrong'
            with self.assertRaisesRegex(ValueError, 'postimage'):
                patcher.plan_updates({target:[entry]})

    def test_later_file_failure_leaves_all_installed_files_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp)/'first.py', Path(tmp)/'second.py'
            first.write_text('first'); second.write_text('unrecognized')
            with self.assertRaises(ValueError):
                patcher.plan_updates({first:[edit('first','fixed')], second:[edit('second','fixed')]})
            self.assertEqual(first.read_text(),'first')
            self.assertEqual(second.read_text(),'unrecognized')
