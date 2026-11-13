"""Coordinate changes preserve semantic configuration; no external services."""
from copy import deepcopy
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dbt_bench import io
from dbt_bench.local_config import load,expand
from release_tools import config_for,same_task_semantics,ROOT,qwen_installation_identity
from tests.helpers import fixture_root,qwen_bundle_fixture

class PortableControls(unittest.TestCase):
    def test_nested_local_paths_and_casefold_allocation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'local.toml';p.write_text('allocation="EXAMPLE42"\nstorage_root="'+d+'/data"\nartifact_root="${STORAGE_ROOT}/results"\n')
            with patch.dict(os.environ,{'DBT_BENCH_LOCAL_CONFIG':str(p)}):
                self.assertEqual(expand('${ARTIFACT_ROOT}/x'),d+'/data/results/x')
                self.assertEqual(expand('${ALLOCATION_LOWER}'),'example42')

    def test_task_relocation_only_image(self):
        a='[environment]\ndocker_image="/old/image.sif"\n[agent]\ntimeout_sec=4000\n'
        b=a.replace('/old/image.sif','/new/image.sif');same_task_semantics(a,b)
        with self.assertRaisesRegex(ValueError,'semantics'):same_task_semantics(a,b.replace('4000','600'))

    def test_every_template_has_four_native_attempts_and_fixed_sampling(self):
        rows=io.read_json(ROOT/'catalog/measurement-tasks.json')['tasks']
        self.assertEqual(len(rows),67)
        for r in rows:
            c=r['config_template'];self.assertEqual(c['n_attempts'],4);self.assertEqual(c['n_concurrent_trials'],1)
            self.assertEqual(c['retry']['max_retries'],0);self.assertEqual(c['timeout_multiplier'],1)
            self.assertIsNone(c['agents'][0]['override_timeout_sec'])
            self.assertEqual(c['agents'][0]['kwargs']['config'],io.read_json(ROOT/'inference/qwen-code-settings.json'))

    def test_rebuilt_manifest_binds_actual_hash_without_changing_protocol(self):
        template=deepcopy(io.read_json(ROOT/'catalog/measurement-tasks.json')['tasks'][0]['config_template'])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);fixture_root(root);bundle=qwen_bundle_fixture(root)
            template['agents'][0]['kwargs']['installation_bundle_sha256']=io.file_hash(bundle)
            with patch('release_tools.ROOT',root):
                original=config_for(template,root/'member',root/'task',{'qwen_bundle':str(bundle)})
                original_receipt=qwen_installation_identity(original)
                manifest=io.read_json(bundle);manifest['source_installation']={'path':'different-builder/result.json','sha256':'a'*64}
                io.write_json(bundle,manifest)
                current=config_for(template,root/'member',root/'task',{'qwen_bundle':str(bundle)})
                receipt=qwen_installation_identity(current)
                self.assertEqual(receipt['content_id'],original_receipt['content_id'])
                self.assertNotEqual(receipt['manifest_sha256'],original_receipt['manifest_sha256'])
                self.assertEqual(current['agents'][0]['kwargs']['installation_bundle_sha256'],io.file_hash(bundle))
                # Only deployment coordinates and the verified manifest binding change.
                for key in ('job_name','jobs_dir'):current[key]=template[key]
                current['tasks'][0]['path']=template['tasks'][0]['path']
                for key in ('installation_bundle','installation_bundle_sha256'):
                    current['agents'][0]['kwargs'][key]=template['agents'][0]['kwargs'][key]
                self.assertEqual(current,template)

    def test_rewritten_manifest_cannot_authorize_different_bundle_bytes(self):
        template=deepcopy(io.read_json(ROOT/'catalog/measurement-tasks.json')['tasks'][0]['config_template'])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);fixture_root(root);bundle=qwen_bundle_fixture(root)
            manifest=io.read_json(bundle);archive=bundle.parent/'archive'
            archive.write_text('tampered but self-consistently rehashed')
            manifest['files']['archive']['sha256']=io.file_hash(archive);io.write_json(bundle,manifest)
            with patch('release_tools.ROOT',root):
                with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                    config_for(template,root/'member',root/'task',{'qwen_bundle':str(bundle)})

    def test_64_tasks_unique_and_native_budgets_fit_full_phase_caps(self):
        batches=io.read_json(ROOT/'release/batches.json')['batches'];keys=[]
        for b in batches:
            keys+=b['task_keys'];self.assertLessEqual(len(b['task_keys']),3)
            self.assertEqual(b['attempt_ids'],[1,2,3,4]);self.assertGreaterEqual(b['walltime_sec']-b['all_phase_caps_sec']-1200,3600)
        self.assertEqual(len(keys),64);self.assertEqual(len(set(keys)),64)
        self.assertNotIn('deb__dbt-fix-repeat-purchase-cohort-revenue',keys)

    def test_split_seed_and_algorithm_remain_predeclared(self):
        r=io.read_json(ROOT/'configs/difficulty-v1/split-rules.json')
        self.assertEqual(r['random_seed'],2026110801);self.assertEqual(r['random_assignments'],10000)
        self.assertTrue(r['training_prohibited']);self.assertTrue(r['approval_required_before_freeze'])
