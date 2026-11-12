from pathlib import Path
import io as bytes_io
import json
import tarfile
import tempfile
import unittest
from difficulty_collect import collect,sha
from difficulty_submission import extract_source


class DifficultySubmissionTest(unittest.TestCase):
    def fixture(self,p):
        project=p/'project';project.mkdir();(project/'model.sql').write_text('select 1')
        db=p/'data.duckdb';db.write_bytes(b'db')
        trial=p/'trial';target=trial/'artifacts/difficulty-submission'
        capture=collect({'task_key':'task','project_root':str(project),'database_paths':[str(db)]},target)
        (trial/'artifacts/manifest.json').write_text(json.dumps([{'source':'/tmp/dbt-bench-difficulty-submission',
             'destination':'artifacts/difficulty-submission','status':'ok','type':'directory'}]))
        (trial/'result.json').write_text(json.dumps({'agent_execution':{'finished_at':'2020-01-01T00:00:00+00:00'},
                                                   'verifier':{'started_at':'2030-01-01T00:00:00+00:00'}}))
        return trial,target,capture

    def test_extracts_exact_source_and_rejects_replaced_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);trial,target,capture=self.fixture(p)
            value=extract_source(trial,'task',p/'extracted')
            self.assertTrue(value['training_prohibited'])
            self.assertEqual((p/'extracted/model.sql').read_text(),'select 1')
            (target/'project.tar.gz').write_bytes(b'changed')
            with self.assertRaises(ValueError):extract_source(trial,'task',p/'bad')
            self.assertFalse((p/'bad').exists())

    def test_rejects_traversal_even_if_archive_hash_was_rebound(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);trial,target,capture=self.fixture(p)
            archive=target/'project.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                item=tarfile.TarInfo('../escape.sql');item.size=8
                tar.addfile(item,bytes_io.BytesIO(b'select 1'))
            capture['project_archive']['sha256']=sha(archive)
            capture['project_files'][0]['path']='../escape.sql'
            (target/'capture.json').write_text(json.dumps(capture))
            with self.assertRaises(ValueError):extract_source(trial,'task',p/'bad')
            self.assertFalse((p/'escape.sql').exists())

    def test_post_verifier_capture_cannot_be_used_as_agent_submission(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);trial,target,capture=self.fixture(p)
            r=json.loads((trial/'result.json').read_text());r['verifier']['started_at']='2020-01-01T00:00:00+00:00'
            (trial/'result.json').write_text(json.dumps(r))
            with self.assertRaises(ValueError):extract_source(trial,'task',p/'bad')
