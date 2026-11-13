import json
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
