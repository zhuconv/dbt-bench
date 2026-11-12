from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dbt_bench import io
from phase4_launch import check_batch


class NoModelLaunchTests(unittest.TestCase):
    def fixture(self, root):
        config = {'job_name': root.name, 'jobs_dir': str(root / 'jobs'),
                  'environment': {'type': 'singularity'},
                  'agents': [{'import_path': 'phase4_case_agent:Phase4CaseAgent'}]}
        io.write_json(root / 'config.yaml', config)
        manifest = {'phase': 4, 'model_requests': 0, 'tasks': {}, 'tools': [],
                    'config': io.evidence_ref(root / 'config.yaml', root)}
        io.write_json(root / 'manifest.json', manifest)
        return config, manifest

    def test_a_model_endpoint_cannot_be_launched_as_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config, manifest = self.fixture(root)
            self.assertEqual(check_batch(root, root), manifest)
            config['agents'][0]['base_url'] = 'https://endpoint.invalid/v1'
            io.write_json(root / 'config.yaml', config)
            manifest['config'] = io.evidence_ref(root / 'config.yaml', root); io.write_json(root / 'manifest.json', manifest)
            with self.assertRaisesRegex(ValueError, 'not a no-model'):
                check_batch(root, root)

    def test_changed_config_or_wrong_adapter_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config, manifest = self.fixture(root)
            config['agents'][0]['import_path'] = 'ordinary_model_agent:Agent'
            io.write_json(root / 'config.yaml', config)
            with self.assertRaisesRegex(ValueError, 'configuration changed'):
                check_batch(root, root)
            manifest['config'] = io.evidence_ref(root / 'config.yaml', root); io.write_json(root / 'manifest.json', manifest)
            with self.assertRaisesRegex(ValueError, 'Only reviewed no-model'):
                check_batch(root, root)


if __name__ == '__main__':
    unittest.main()
