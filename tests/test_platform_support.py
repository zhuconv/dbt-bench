from copy import deepcopy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import tomllib
from dbt_bench import io
from platform_support import platform_name,check_host,image_identity,runtime_inventory

ROOT=Path(__file__).resolve().parents[1]

class Platforms(unittest.TestCase):
    def test_architecture_guard_and_no_implicit_x86_image(self):
        with patch('platform.machine',return_value='aarch64'),patch('platform.system',return_value='Linux'):
            check_host({'platform':'linux-arm64'})
            with self.assertRaisesRegex(ValueError,'host'):check_host({'platform':'linux-amd64'})
        row=io.read_json(ROOT/'catalog/measurement-tasks.json')['tasks'][0]
        arm=image_identity(row,{'platform':'linux-arm64'},ROOT)
        self.assertTrue(arm['oci_digest'].startswith('sha256:'))
        with self.assertRaisesRegex(ValueError,'published'):image_identity(row,{'platform':'linux-amd64'},ROOT)

    def test_runtime_patch_changes_only_manifest_platform_gate(self):
        before=io.read_json(ROOT/'release/harbor-package-files.json');after=io.read_json(ROOT/'release/harbor-amd64-package-files.json')
        differences=[(a['path'],b['path']) for a,b in zip(before,after) if a!=b]
        self.assertEqual(differences,[('agents/installed/qwen_code.py','agents/installed/qwen_code.py')])
        self.assertEqual(len(before),len(after))
        patch=io.read_json(ROOT/'patches/harbor-025-qwen-platform-v1/metadata.json')
        self.assertIn('linux-x64',patch['after_text']);self.assertNotIn('timeout',patch['after_text'])

    def test_shared_inference_versions_and_protocol_unchanged(self):
        def versions(p):return {v['name']:v.get('version') for v in tomllib.loads(p.read_text())['package']}
        arm=versions(ROOT/'inference/uv.lock');x64=versions(ROOT/'inference/x86_64/uv.lock')
        self.assertEqual(x64['vllm'],'0.19.0')
        self.assertFalse({k for k in arm.keys()&x64.keys() if arm[k]!=x64[k]})
        spec=io.read_json(ROOT/'inference/qwen3.6-35b-a3b.json');args=spec['server_args']
        self.assertEqual(args[args.index('--max-model-len')+1],'131072')
        self.assertEqual(args[args.index('--dtype')+1],'bfloat16')

    def test_x64_bundle_selects_glibc_and_seventeen_fixed_packages(self):
        from build_qwen_platform import selected
        lock=io.read_json(ROOT/'runtime/qwen-code/linux-x64/package-lock.json');entries=selected(lock['packages'],'x64')
        self.assertEqual(len(entries),17)
        self.assertIn('node_modules/@img/sharp-linux-x64',entries)
        self.assertFalse(any('linuxmusl' in n or 'arm64' in n for n in entries))
        pin=io.read_json(ROOT/'runtime/qwen-code/linux-x64/bundle.lock.json')
        for reference in pin['locks']:self.assertTrue(io.evidence_ok(reference,ROOT))

    def test_all_images_have_pinned_group_and_original_release_input(self):
        images=io.read_json(ROOT/'images/platform-images.json')['images'];groups=io.read_json(ROOT/'images/locks/software-groups.json')
        self.assertEqual(len(images),33);self.assertEqual(len(groups),5)
        for i in images:
            self.assertIn(i['base_group'],groups)
            for filename in ('apt.txt','python.txt','server.txt'):
                self.assertTrue((ROOT/'images/locks'/i['base_group']/filename).is_file())
            self.assertEqual(i['initial_snapshot']['status'],'published')
            self.assertTrue((ROOT/i['native_initializer']).is_file())


class HistoricalCodeImport(unittest.TestCase):
    """Real Git histories and platform proofs, without mocking import validation."""
    def git(self,*args):
        return subprocess.run(['git','-c','core.hooksPath=/dev/null','-C',str(self.repo),*args],
            check=True,capture_output=True,text=True).stdout.strip()

    def setUp(self):
        from platform_support import code_identity
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name)/'repo';self.repo.mkdir()
        self.proof=Path(self.tmp.name)/'proof';self.proof.mkdir()
        self.git('init','-b','main');self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
        self.git('config','commit.gpgsign','false')
        for name in ('scripts/run.py','src/old.py','runtime/qwen-code/lock.json',
                     'scripts/worker.py','scripts/ledger_check.py','scripts/README.md'):
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'original\r\n')
        for name in ('inventory','installer'):(self.proof/name).write_text(name)
        bundle={'kind':'dbt-bench-qwen-bundle-v1','platform':'linux-arm64',
            'qwen_version':'0.26.5','node_version':'22.16.0','npm_version':'10.9.2','locks':[],
            'files':{name:{'path':name,'sha256':io.file_hash(self.proof/name)} for name in ('inventory','installer')}}
        bundle['files']['archive']={'path':'archive','sha256':'0'*64}
        io.write_json(self.proof/'bundle.json',bundle)
        content={k:v for k,v in bundle.items() if k!='files'}
        content['files']={k:v['sha256'] for k,v in bundle['files'].items()}
        io.write_json(self.repo/'sources.lock.yaml',{'qwen_code':{'installation_bundle':content}})
        io.write_json(self.repo/'release/assets.json',{'assets':[{'id':'image','sha256':'1'*64,
            'distribution':{'uri':'oras://example.invalid/image','registry_manifest_digest':'sha256:'+'2'*64}}]})
        io.write_json(self.repo/'release/harbor-package-files.json',[])
        self.row={'image_asset':'image'};deployment={'platform':'linux-arm64'}
        self.git('add','.');self.git('commit','-m','prepared code')
        self.identity={'deployment':deployment,'image_identity':image_identity(self.row,deployment,self.repo),
            'qwen_installation':{'content':content,'content_id':io.digest(content),
                'manifest_sha256':io.file_hash(self.proof/'bundle.json')},
            'config':{'agents':[{'kwargs':{'installation_bundle_sha256':io.file_hash(self.proof/'bundle.json')}}]},
            'code_identity':code_identity(self.repo),
            'runtime_patch':{'package_inventory':{'sha256':io.file_hash(self.repo/'release/harbor-package-files.json')}}}

    def verify(self,identity=None):
        from platform_support import verify_import_platform
        verify_import_platform(identity or self.identity,self.proof,self.row,self.repo,
            code_repo=self.repo,owner_main='refs/heads/main')

    def test_prepared_code_import_survives_later_scripts_commit(self):
        self.verify()
        (self.repo/'scripts/run.py').write_text('changed after preparation\n')
        (self.repo/'scripts/new.py').write_text('added after preparation\n')
        (self.repo/'src/old.py').unlink()
        self.git('add','.');self.git('commit','-m','later scripts changes')
        self.assertNotEqual(self.git('rev-parse','main'),self.identity['code_identity']['commit'])
        self.verify()  # Includes the historical file set, even files removed on main.

    def test_unaccepted_commit_or_changed_inventory_is_rejected(self):
        from platform_support import code_identity
        self.git('checkout','-b','unaccepted')
        (self.repo/'scripts/run.py').write_text('unmerged code\n')
        self.git('add','.');self.git('commit','-m','unaccepted change')
        unaccepted=deepcopy(self.identity);unaccepted['code_identity']=code_identity(self.repo)
        with self.assertRaisesRegex(ValueError,'not an ancestor of owner main'):self.verify(unaccepted)
        self.git('checkout','main')
        # The dirty checkout agrees with the worker's forged inventory, but the commit does not.
        (self.repo/'scripts/run.py').write_text('uncommitted code\n')
        changed=deepcopy(self.identity);changed['code_identity']=code_identity(self.repo)
        with self.assertRaisesRegex(ValueError,'Unreviewed code version'):self.verify(changed)
        for mutation in ('missing','extra','duplicate','unknown_commit'):
            with self.subTest(mutation=mutation):
                changed=deepcopy(self.identity);code=changed['code_identity']
                if mutation=='missing':code['files'].pop()
                elif mutation=='extra':code['files'].append({'path':'scripts/extra.py','sha256':'0'*64})
                elif mutation=='duplicate':code['files'][-1]=code['files'][0]
                else:code['commit']='f'*40
                code['content_id']=io.digest(code['files'])
                with self.assertRaises(ValueError):self.verify(changed)
