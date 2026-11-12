import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('difficulty_collect', ROOT/'scripts/difficulty_collect.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DifficultyCaptureTest(unittest.TestCase):
    def test_keeps_declared_db_wal_and_source_without_writing_input(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); project=p/'project';project.mkdir()
            (project/'models').mkdir();(project/'models/m.sql').write_text('select 1')
            (project/'node_modules').mkdir();(project/'node_modules/private.json').write_text('{}')
            db=p/'input.duckdb';db.write_bytes(b'opaque database bytes')
            Path(str(db)+'.wal').write_bytes(b'wal')
            r=module.collect({'task_key':'x','project_root':str(project),'database_paths':[str(db)]},p/'out')
            self.assertTrue(r['complete']);self.assertEqual(db.read_bytes(),b'opaque database bytes')
            self.assertEqual(len(r['databases']),2)
            with tarfile.open(p/'out/project.tar.gz') as archive:
                self.assertEqual(archive.getnames(),['models/m.sql'])
                self.assertEqual(archive.extractfile('models/m.sql').read(),b'select 1')

    def test_missing_database_or_external_source_cannot_be_complete(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);project=p/'project';project.mkdir()
            (p/'outside.sql').write_text('outside');(project/'m.sql').symlink_to(p/'outside.sql')
            r=module.collect({'task_key':'x','project_root':str(project),'database_paths':[str(p/'missing')]},p/'out')
            self.assertFalse(r['complete']);self.assertTrue(r['errors'])

    def test_refuses_preexisting_capture(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):
                module.collect({'task_key':'x','project_root':d,'database_paths':[]},Path(d))
