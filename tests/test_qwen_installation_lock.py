import importlib.util,json,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('qwen_lock',ROOT/'scripts/freeze_qwen_dependencies.py')
mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)


class QwenInstallationLockTests(unittest.TestCase):
    def test_hoisting_equivalence_preserves_package_and_file_identity(self):
        file={'path':'node_modules/@scope/tool/main.js','sha256':'a'*64,'size':7}
        nested={**file,'path':'node_modules/@qwen-code/qwen-code/'+file['path']}
        self.assertEqual(mod.package_inventory([file])[0],mod.package_inventory([nested])[0])
        changed={**nested,'sha256':'b'*64}
        self.assertNotEqual(mod.package_inventory([file])[0],mod.package_inventory([changed])[0])
        with self.assertRaises(ValueError):mod.package_inventory([file,changed])

    def test_only_generated_module_metadata_is_omitted(self):
        files=[{'path':p,'sha256':'a'*64,'size':1} for p in ['node_modules/.bin/qwen',
               'node_modules/.package-lock.json','node_modules/tool/vendor/.bin/real-source']]
        inventory,omitted=mod.package_inventory(files)
        self.assertEqual(len(omitted),2)
        self.assertIn(('tool','vendor/.bin/real-source'),inventory)
        with self.assertRaises(ValueError):mod.package_inventory([{**files[0],'path':'../outside'}])

    def test_exact_hash_libc_repair_and_idempotence(self):
        patch=ROOT/'patches/npm-10-lock-libc-metadata-v1'
        metadata=mod.read_json(patch/'metadata.json')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'lock.json';path.write_bytes((patch/'package-lock-before.json').read_bytes())
            mod.repair_libc_metadata(path,patch)
            self.assertEqual(mod.file_hash(path),metadata['after_sha256'])
            mod.repair_libc_metadata(path,patch)
            path.write_text(path.read_text()+' ')
            with self.assertRaises(ValueError):mod.repair_libc_metadata(path,patch)

    def test_portable_receipt_is_complete_for_fixed_package_set(self):
        receipt=mod.read_json(ROOT/'runtime/qwen-code/native-installation.json')
        packages={name for name,version in receipt['dependency_versions']}
        self.assertEqual(len(packages),17)
        entries=receipt['package_files']
        self.assertEqual(len({(r['package'],r['path']) for r in entries}),len(entries))
        self.assertEqual({r['package'] for r in entries},packages)
