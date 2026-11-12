import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prepare_spider_witness_batch import candidate_files
from spider_witness_build import main


class SpiderWitnessTests(unittest.TestCase):
    def test_changed_public_sql_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory/'files/models').mkdir(parents=True)
            (directory/'files/models/example.sql').write_text('select 2')
            metadata = {'files':[{'path':'models/example.sql',
                'sha256':hashlib.sha256(b'select 1').hexdigest()}], 'project_yml_changed':False}
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                candidate_files(directory,metadata)

    def test_payload_cannot_target_files_outside_models(self):
        metadata = {'files':[{'path':'models/../../gold.duckdb','sha256':'unused'}],
                    'project_yml_changed':False}
        with self.assertRaisesRegex(ValueError, 'relative model paths'):
            candidate_files(Path('/unused'),metadata)

    def run_builder(self, mode, dbt_status):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, workspace, logs = (root/'source',root/'workspace',root/'logs')
            for path in (source,workspace,logs):path.mkdir()
            (workspace/'dbt_packages').mkdir()
            (workspace/'dbt_packages/frozen.sql').write_text('select 1')
            shim = b'synthetic clock fixture'
            (source/'fakeclock.so').write_bytes(shim)
            (source/'candidate.json').write_text(json.dumps({'task_key':'spider2__lever001',
                'mode':mode,'files':[],'seed_first':False,
                'clock_shim':{'sha256':hashlib.sha256(shim).hexdigest()}}))
            calls=[]
            def run(command, **kwargs):
                calls.append((command,dict(kwargs['env'])))
                if command[0]=='python3':
                    return SimpleNamespace(returncode=0,stdout=json.dumps({
                        'python_utc':'2024-09-08T12:00:00+00:00',
                        'duckdb_now':'2024-09-08 12:00:00+00:00'}),stderr='')
                kwargs['stdout'].write('actual diagnostic dbt failure\n' if dbt_status else 'dbt run completed\n')
                return SimpleNamespace(returncode=dbt_status)
            with patch('spider_witness_build.subprocess.run',side_effect=run):
                with patch.dict('os.environ',{'LD_PRELOAD':'untrusted-parent.so','FAKECLOCK_OFFSET':'1'}):
                    result=main(source,workspace,logs)
                    import os
                    self.assertEqual(os.environ['LD_PRELOAD'],'untrusted-parent.so')
            report=json.loads((logs/'witness-build.json').read_text())
            return result,report,calls

    def test_failed_build_keeps_failure_and_diagnostics(self):
        result,report,calls=self.run_builder('real-clock',2)
        self.assertEqual(result,2)
        self.assertEqual(report['commands'][0]['returncode'],2)
        self.assertTrue(report['packages_unchanged'])
        self.assertNotIn('LD_PRELOAD',calls[-1][1])
        self.assertNotIn('FAKECLOCK_OFFSET',calls[-1][1])

    def test_clock_is_scoped_to_build_subprocesses(self):
        result,report,calls=self.run_builder('fixed-clock',0)
        self.assertEqual(result,0)
        self.assertTrue(calls[-1][1]['LD_PRELOAD'].endswith('/fakeclock.so'))
        self.assertEqual(calls[-1][0],['dbt','run','--profiles-dir','/workspace'])
        self.assertEqual(report['clock_diagnostic']['target_date'],'2024-09-08')
        self.assertFalse(any('/tests' in str(command) for command,_ in calls))


if __name__=='__main__':unittest.main()
