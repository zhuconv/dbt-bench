"""Dependency restoration must follow the actual submitted immutable pins."""
from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
HAS_YAML=importlib.util.find_spec('yaml')is not None
if HAS_YAML:
    from difficulty_ade_prepare_audit_v3 import locked_dependencies


@unittest.skipUnless(HAS_YAML,'Locked runtime PyYAML required')
class DependencyPins(unittest.TestCase):
    def probe(self,declaration,lock):
        with TemporaryDirectory()as directory:
            p=Path(directory);files=[]
            for name,data in [('packages.yml',declaration),('package-lock.yml',lock)]:
                f=p/name;f.write_text(data);files.append({'path':name,'captured_source':{'path':str(f)}})
            return locked_dependencies({'source_files':files})
    def test_exact_original_version_is_preserved(self):
        r=self.probe('packages:\n- package: dbt-labs/dbt_utils\n  version: 1.3.2\n',
            'packages:\n- name: dbt_utils\n  package: dbt-labs/dbt_utils\n  version: 1.3.2\n')
        self.assertEqual(r['resolved'],[{'package':'dbt-labs/dbt_utils','version':'1.3.2'}])
    def test_version_range_is_not_silently_resolved(self):
        with self.assertRaises(ValueError):self.probe('packages:\n- package: dbt-labs/dbt_utils\n  version: ">=1.3"\n','packages: []\n')
    def test_lock_disagreement_is_rejected(self):
        with self.assertRaises(ValueError):self.probe('packages:\n- package: dbt-labs/dbt_utils\n  version: 1.3.2\n',
            'packages:\n- package: dbt-labs/dbt_utils\n  version: 1.3.3\n')
    def test_missing_lock_is_unknown_not_default_latest(self):
        with self.assertRaises(ValueError):locked_dependencies({'source_files':[]})

if __name__=='__main__':unittest.main()
