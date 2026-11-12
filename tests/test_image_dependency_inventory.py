import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('image_dependency_probe', Path(__file__).resolve().parents[1] / 'scripts/image_dependency_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class DependencyInventoryTests(unittest.TestCase):
    def test_same_size_file_change_invalidates_tree(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'package.sql'
            p.write_text('select 1')
            before = probe.package_tree(d)
            p.write_text('select 2')
            self.assertNotEqual(before['sha256'], probe.package_tree(d)['sha256'])

    def test_external_link_target_is_not_part_of_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'packages'
            root.mkdir()
            target = Path(d) / 'outside'
            target.mkdir()
            (target / 'secret').write_text('one')
            (root / 'linked').symlink_to(target, target_is_directory=True)
            before = probe.package_tree(root)
            (target / 'secret').write_text('two')
            after = probe.package_tree(root)
            self.assertEqual(before, after)
            self.assertEqual(after['links'], 1)
            self.assertEqual(after['files'], 0)

    def test_mode_and_link_text_are_bound(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / 'file'
            p.write_text('abc')
            p.chmod(0o644)
            first = probe.package_tree(root)['sha256']
            p.chmod(0o755)
            self.assertNotEqual(first, probe.package_tree(root)['sha256'])
            link = root / 'link'
            link.symlink_to('file')
            first = probe.package_tree(root)['sha256']
            link.unlink()
            link.symlink_to('./file')
            self.assertNotEqual(first, probe.package_tree(root)['sha256'])

    def test_missing_packages_are_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(probe.package_tree(Path(d) / 'missing')['present'], False)


if __name__ == '__main__':
    unittest.main()
