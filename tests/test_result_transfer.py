from copy import deepcopy
import io as streamio
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from dbt_bench import io
from result_transfer import (ROOT, export_task, verify_import, safe_unpack, task_slots,
                             normalized_config, download_raw, import_ledger)


class Transfers(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        row=deepcopy(io.read_json(ROOT/'catalog/measurement-tasks.json')['tasks'][0]);self.row=row
        self.key=row['task_key'];self.batch=self.root/'batch';self.run=self.batch/'members'/self.key
        self.run.mkdir(parents=True);task=self.run/'task';(task/'tests').mkdir(parents=True)
        (task/'tests/test.sh').write_text('exit 0')
        original='[environment]\ndocker_image="canonical"\n[agent]\ntimeout_sec=600\n'
        (task/'task.toml').write_text(original);self.source=self.root/'source';shutil.copytree(task,self.source)
        row['task_tree_sha256']=io.tree_hash(task);row['native_tests_sha256']=io.tree_hash(task/'tests')
        (task/'task.toml').write_text(original.replace('canonical','local.sif'))
        bundle=self.root/'bundle';bundle.mkdir()
        for name in ('inventory','installer'):(bundle/name).write_text(name)
        bm={'files':{n:{'path':n,'sha256':io.file_hash(bundle/n)} for n in ('inventory','installer')}}
        io.write_json(bundle/'bundle.json',bm)
        config=deepcopy(row['config_template']);config.update(job_name='job',jobs_dir=str(self.run/'jobs'))
        config['tasks'][0]['path']=str(task);config['agents'][0]['kwargs']['installation_bundle']=str(bundle/'bundle.json')
        slots=[{'task_key':self.key,'attempt':n} for n in (1,2,3,4)]
        identity={'task_key':self.key,'task_version':row['task_version'],'qualified_review_asset':row['qualified_review_asset'],'config':config,'attempt_ids':[1,2,3,4],
            'server_seed':42,'canonical_task_hash':row['task_tree_sha256'],'actual_task_hash':io.tree_hash(task),
            'deployment':{'vllm_executable':'/fixture/vllm','model_snapshot':'/fixture/model'},'runtime_patch':{},'qwen_installation':{},'code_identity':{'commit':'test'},'image_identity':{}}
        io.write_json(self.run/'config.yaml',config)
        io.write_json(self.run/'manifest.json',{'identity':identity,'experiment_id':io.digest(identity),
            'config_hash':io.file_hash(self.run/'config.yaml'),'planned':slots,'training_prohibited':True})
        bi={'batch_id':'batch-01','server_seed':42,'walltime_sec':86400,'planned':slots,
            'members':[{'run':str(self.run),'manifest':io.evidence_ref(self.run/'manifest.json',self.repo)}]}
        io.write_json(self.batch/'manifest.json',{'identity':bi,'experiment_id':io.digest(bi)})
        io.write_json(self.run/'launch-result.json',{'finished_at':999,'finalization_status':'recorded_after_cleanup'})
        io.write_json(self.batch/'inference-runtime.json',{'machine':'aarch64','python':'fixture','packages':{'vllm':'0.19.0','torch':'2.10.0+cu128','torchaudio':'2.10.0+cu128','torchvision':'0.25.0+cu128'}})
        self.declaration={'id':'batch-01','server_seed':42,'walltime_sec':86400,'task_keys':[self.key]}
        io.write_json(self.repo/'release/batches.json',{'batches':[self.declaration]})
        io.write_json(self.repo/'catalog/measurement-tasks.json',{'tasks':[row]})
        for file in ('release/model-files.json','inference/qwen3.6-35b-a3b.json','release/harbor-package-files.json'):
            io.write_json(self.repo/file,{'revision':'fixed'})
        spec=io.read_json(ROOT/'inference/qwen3.6-35b-a3b.json');io.write_json(self.repo/'inference/qwen3.6-35b-a3b.json',spec)
        io.write_json(self.batch/'server-command.json',{'argv':['/fixture/vllm','serve','/fixture/model','--served-model-name',spec['served_model_name'],*spec['server_args'],'--override-generation-config',json.dumps(spec['generation_defaults']),'--seed','42']})
        for n in (1,2,3,4):
            trial=self.run/f'jobs/job/trial-{n}'
            io.write_json(trial/'result.json',{'id':str(n),'trial_name':f'trial-{n}',
                'started_at':f'2026-11-12T00:0{n}:00Z','finished_at':f'2026-11-12T00:0{n}:30Z',
                'config':{'task':config['tasks'][0],'agent':config['agents'][0],'environment':config['environment'],
                    'verifier':config['verifier'],**{k:config[k] for k in ('timeout_multiplier','agent_timeout_multiplier',
                    'verifier_timeout_multiplier','agent_setup_timeout_multiplier','environment_build_timeout_multiplier','install_only')}},
                'verifier_result':{'rewards':{'reward':1 if n<3 else 0}},
                'exception_info':{'type':'AgentTimeoutError'} if n==4 else None})
            (trial/'agent').mkdir();(trial/'agent/raw.json').write_text('raw tool error')
        self.export=self.root/'export'
        with patch('release_tools.task_index',return_value={self.key:row}),patch('release_tools.asset',return_value=self.source),patch('platform_support.bind_bundle'),patch('platform_support.image_identity',return_value={}):
            self.receipt=export_task(self.batch,self.key,self.export,self.repo)
        self.summary=io.read_json(self.export/'summary.json');self.stage=self.root/'stage';self.stage.mkdir()
        safe_unpack(Path(self.receipt['archive']),self.stage)

    def verify(self):
        with patch('platform_support.verify_import_platform'):
            return verify_import(self.stage,self.summary,self.declaration,self.row,self.repo,
                code_repo=self.repo,owner_main='refs/heads/main')

    def test_roundtrip_retains_raw_failures_and_four_slots(self):
        self.verify();self.assertEqual(len(self.summary['slots']),4)
        self.assertEqual(self.summary['slots'][3]['exception_info']['type'],'AgentTimeoutError')
        self.assertEqual(self.summary['slots'][0]['duration_sec'],30)
        self.assertIsNone(self.summary['slots'][0]['joint_success'])
        self.assertEqual((self.stage/'member/jobs/job/trial-1/agent/raw.json').read_text(),'raw tool error')

    def test_tampered_raw_rejected(self):
        (self.stage/'member/jobs/job/trial-1/agent/raw.json').write_text('changed')
        with self.assertRaisesRegex(ValueError,'inventory'):self.verify()

    def test_wrong_seed_budget_or_task_rejected(self):
        for key,value in [('server_seed',43),('walltime_sec',18000)]:
            old=self.declaration[key];self.declaration[key]=value
            with self.assertRaises(ValueError):self.verify()
            self.declaration[key]=old
        self.row['config_template']['timeout_multiplier']=2
        with self.assertRaisesRegex(ValueError,'config/budgets'):self.verify()

    def test_unsafe_archive_rejected(self):
        for name in ('../escape','/absolute'):
            f=self.root/'unsafe.tar.gz'
            with tarfile.open(f,'w:gz') as t:
                info=tarfile.TarInfo(name);info.size=1;t.addfile(info,streamio.BytesIO(b'x'))
            with self.assertRaises(ValueError):safe_unpack(f,self.root/'unpack')

    def test_missing_tail_is_explicit_and_duplicate_is_error(self):
        shutil.rmtree(self.run/'jobs/job/trial-4')
        self.assertEqual(task_slots(self.run,self.key)[3]['status'],'not_started')
        shutil.copytree(self.run/'jobs/job/trial-1',self.run/'jobs/job/duplicate')
        with self.assertRaisesRegex(ValueError,'Duplicate'):task_slots(self.run,self.key)

    def test_unfinalized_not_exportable(self):
        io.write_json(self.run/'launch-result.json',{'finished_at':1,'finalization_status':'cleanup_pending'})
        with self.assertRaisesRegex(ValueError,'finalized'):task_slots(self.run,self.key)

    def test_archive_hash_checked(self):
        raw={'url':'https://github.com/a/b/releases/download/raw-v1/raw.tar.gz','name':'raw.tar.gz',
             'sha256':'0'*64,'bytes':3}
        with patch('urllib.request.urlopen',return_value=streamio.BytesIO(b'abc')):
            with self.assertRaisesRegex(ValueError,'checksum'):download_raw(raw,self.root/'bad.tar.gz')

    def test_import_reads_owner_main_not_worktree_and_is_idempotent(self):
        def g(*args):return subprocess.run(['git','-C',str(self.repo),*args],check=True,capture_output=True,text=True)
        g('init','-b','main');g('config','user.name','Fixture');g('config','user.email','fixture@example.invalid')
        g('remote','add','upstream','https://github.com/zhuconv/dbt-bench.git')
        raw=Path(self.receipt['archive']);summary=deepcopy(self.summary)
        summary.update(worker='fixture',published_at='2026-11-12T00:00:00Z',raw_archive={
            'name':'raw.tar.gz','url':'https://github.com/a/b/releases/download/raw-v1/raw.tar.gz',
            'sha256':io.file_hash(raw),'bytes':raw.stat().st_size})
        summary_path=self.repo/f'ledger/results/batch-01/{self.key}/summary.json'
        io.write_json(summary_path,summary)
        io.write_json(self.repo/'ledger/claims/batch-01/fixture.json',{'batch':'batch-01','worker':'fixture','state':'finished'})
        g('add','.');g('commit','-m','accepted fixture');g('update-ref','refs/remotes/upstream/main','HEAD')
        summary_path.write_text('unmerged tampering')
        def download(entry,target):
            with patch('urllib.request.urlopen',return_value=raw.open('rb')):download_raw(entry,target)
        with patch('result_transfer.download_raw',side_effect=download),patch('platform_support.verify_import_platform') as platform_check:
            output=self.root/'central'
            a=import_ledger(self.repo,output,root=self.repo);b=import_ledger(self.repo,output,root=self.repo)
            self.assertEqual(a,b);self.assertEqual(a['slots'],4)
            self.assertTrue((output/'trials.jsonl').is_file())
            self.assertEqual(platform_check.call_count,2)
            for call in platform_check.call_args_list:
                self.assertEqual(call.kwargs,{'code_repo':self.repo,'owner_main':a['accepted_main_commit']})

    def test_server_seed_tamper_rejected_even_with_updated_archive_inventory(self):
        p=self.stage/'batch/server-command.json';value=io.read_json(p);value['argv'][-1]='999';io.write_json(p,value)
        io.write_json(self.stage/'inventory.json',[v for v in io.tree_manifest(self.stage) if v['path']!='inventory.json'])
        with self.assertRaisesRegex(ValueError,'server command'):self.verify()

    def test_archive_writing_through_symlink_is_rejected(self):
        f=self.root/'links.tar.gz'
        with tarfile.open(f,'w:gz') as t:
            link=tarfile.TarInfo('link');link.type=tarfile.SYMTYPE;link.linkname='directory';t.addfile(link)
            file=tarfile.TarInfo('link/payload');file.size=1;t.addfile(file,streamio.BytesIO(b'x'))
        with self.assertRaisesRegex(ValueError,'through a symlink'):safe_unpack(f,self.root/'unpack')

    def test_wrong_actual_trial_config_is_not_a_valid_slot(self):
        p=self.run/'jobs/job/trial-1/result.json';v=io.read_json(p);v['config']['agent']['model_name']='wrong-model';io.write_json(p,v)
        with self.assertRaisesRegex(ValueError,'Actual native trial'):task_slots(self.run,self.key)
