import importlib.util
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'qwen_bundle', ROOT / 'runtime/qwen-code/install_bundle.py')
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


class QwenBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / 'source/nvm'
        (self.tree / 'bin').mkdir(parents=True)
        (self.tree / 'lib').mkdir()
        program = self.tree / 'lib/cli.js'
        program.write_text('#!/usr/bin/env node\n')
        program.chmod(0o755)
        (self.tree / 'bin/qwen').symlink_to('../lib/cli.js')
        for directory in (self.tree, self.tree / 'bin', self.tree / 'lib'):
            directory.chmod(0o755)
        self.archive = self.root / 'bundle.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as stream:
            stream.add(self.tree, arcname='nvm')
        self.inventory = self.root / 'inventory.json'
        self.inventory.write_text(json.dumps(bundle.inventory(self.tree)))
        self.target = self.root / 'home/.nvm'

    def install(self):
        return bundle.install(self.archive, self.inventory, self.target,
                              bundle.sha256(self.archive), bundle.sha256(self.inventory))

    def test_exact_install_replay_and_changed_tree_refusal(self):
        self.assertTrue(self.install())
        self.assertEqual(os.readlink(self.target / 'bin/qwen'), '../lib/cli.js')
        self.assertFalse(self.install())
        (self.target / 'lib/cli.js').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.install()
        self.assertEqual((self.target / 'lib/cli.js').read_text(), 'changed')

    def test_install_modes_do_not_depend_on_task_umask(self):
        previous = os.umask(0o077)
        try:
            self.assertTrue(self.install())
        finally:
            os.umask(previous)
        self.assertEqual(bundle.inventory(self.target), bundle.inventory(self.tree))

    def test_corrupt_archive_and_inventory_rejected_before_install(self):
        archive_hash, inventory_hash = bundle.sha256(self.archive), bundle.sha256(self.inventory)
        for path in (self.archive, self.inventory):
            original = path.read_bytes()
            path.write_bytes(original + b'corrupt')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                bundle.install(self.archive, self.inventory, self.target, archive_hash, inventory_hash)
            self.assertFalse(self.target.exists())
            path.write_bytes(original)

    def test_wrong_contents_leave_no_partial_install(self):
        self.inventory.write_text('[]')
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.install()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob('.dbt-bench-qwen-*')), [])

    def test_archive_escape_and_link_escape_are_rejected(self):
        for path, link in [('nvm/../../escape', None), ('nvm/link', '../../escape')]:
            with self.subTest(path=path, link=link):
                with tarfile.open(self.archive, 'w:gz') as stream:
                    entry = tarfile.TarInfo(path)
                    if link:
                        entry.type = tarfile.SYMTYPE
                        entry.linkname = link
                        stream.addfile(entry)
                    else:
                        entry.size = 1
                        stream.addfile(entry, io.BytesIO(b'x'))
                with self.assertRaises((ValueError, tarfile.FilterError)):
                    self.install()
                self.assertFalse(self.target.exists())
                self.assertFalse((self.root / 'escape').exists())

    def test_existing_destination_symlink_is_not_followed(self):
        self.target.parent.mkdir()
        self.target.symlink_to(self.tree)
        with self.assertRaisesRegex(ValueError, 'existing symlink'):
            self.install()
        self.assertEqual((self.tree / 'lib/cli.js').read_text(), '#!/usr/bin/env node\n')

    def test_apptainer_cross_device_rename_uses_verified_copy(self):
        with patch.object(bundle.os, 'rename', side_effect=OSError(errno.EXDEV, 'cross device')):
            self.assertTrue(self.install())
        self.assertEqual(bundle.inventory(self.target), bundle.inventory(self.tree))
        self.assertFalse(self.install())

    def test_failed_overlay_copy_removes_only_its_new_destination(self):
        with patch.object(bundle.os, 'rename', side_effect=OSError(errno.EXDEV, 'cross device')):
            with patch.object(bundle.shutil, 'copytree', side_effect=OSError('copy failed')):
                with self.assertRaisesRegex(OSError, 'copy failed'):
                    self.install()
        self.assertFalse(self.target.exists())

    def test_other_rename_errors_are_not_hidden_by_copy(self):
        with patch.object(bundle.os, 'rename', side_effect=PermissionError('permission denied')):
            with patch.object(bundle.shutil, 'copytree') as copy:
                with self.assertRaises(PermissionError):
                    self.install()
                copy.assert_not_called()
        self.assertFalse(self.target.exists())


class PreparedBundleTests(unittest.TestCase):
    def test_builder_normalizes_umask_drift_without_changing_bytes_or_links(self):
        spec = importlib.util.spec_from_file_location('build_qwen_modes', ROOT / 'scripts/build_qwen_bundle.py')
        builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / 'nvm'; (tree / 'bin').mkdir(parents=True)
            plain = tree / 'nvm.sh'; plain.write_text('fixed script'); plain.chmod(0o600)
            executable = tree / 'bin/node'; executable.write_text('fixed executable'); executable.chmod(0o700)
            (tree / 'bin/qwen').symlink_to('node')
            tree.chmod(0o700); (tree / 'bin').chmod(0o700)
            before = {p.name: bundle.sha256(p) for p in (plain, executable)}
            builder.normalize_runtime_modes(tree)
            self.assertEqual(plain.stat().st_mode & 0o777, 0o644)
            self.assertEqual(executable.stat().st_mode & 0o777, 0o755)
            self.assertEqual((tree / 'bin').stat().st_mode & 0o777, 0o755)
            self.assertEqual(os.readlink(tree / 'bin/qwen'), 'node')
            self.assertEqual(before, {p.name: bundle.sha256(p) for p in (plain, executable)})
            first = bundle.inventory(tree); builder.normalize_runtime_modes(tree)
            self.assertEqual(first, bundle.inventory(tree))

    def test_added_files_or_changed_receipt_cannot_be_bundled(self):
        spec = importlib.util.spec_from_file_location('build_qwen', ROOT / 'scripts/build_qwen_bundle.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as tmp:
            installation = Path(tmp)
            path = installation / 'first/node_modules/tool/index.js'
            path.parent.mkdir(parents=True)
            path.write_text('exact package')
            item = {'path': 'node_modules/tool/index.js', 'sha256': bundle.sha256(path), 'size': path.stat().st_size}
            inventory = installation / 'first-inventory.json'
            inventory.write_text(json.dumps([item]))
            receipt = {'package_files': [{**item, 'package': 'tool', 'path': 'index.js'}]}
            builder.verify_modules(installation, receipt)
            extra = path.with_name('unexpected.js')
            extra.write_text('extra')
            with self.assertRaisesRegex(ValueError, 'inventory changed'):
                builder.verify_modules(installation, receipt)
            extra.unlink()
            path.write_text('different package')
            inventory.write_text(json.dumps([{**item, 'sha256': bundle.sha256(path), 'size': path.stat().st_size}]))
            with self.assertRaisesRegex(ValueError, 'locked native receipt'):
                builder.verify_modules(installation, receipt)

    def test_actual_harbor_candidate_checks_identity_before_upload(self):
        python = ROOT / 'runtime/.venv/bin/python'
        if not python.exists():
            self.skipTest('Pinned Harbor runtime not installed')
        result = subprocess.run([str(python), str(ROOT / 'tests/fixtures/qwen_install_candidate_checks.py')],
                                cwd=ROOT, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
