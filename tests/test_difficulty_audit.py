"""Submission reconstruction must not restore deleted or forged model source."""
from copy import deepcopy
import hashlib

import tempfile
import unittest
from pathlib import Path
from importlib.util import find_spec

HAVE_RUNTIME_YAML = find_spec('yaml') is not None
if HAVE_RUNTIME_YAML:
    from prepare_difficulty_audit import source_plan
    from score_difficulty_northwind import assert_delivered_sources


def captured(tmp_path, files):
    rows = []
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
        rows.append({'path': name, 'sha256': hashlib.sha256(data.encode()).hexdigest(),
                     'captured_source': {'path': str(path), 'sha256': hashlib.sha256(data.encode()).hexdigest()}})
    return {'source_dir': str(tmp_path), 'source_files': rows}


@unittest.skipUnless(HAVE_RUNTIME_YAML, 'Source rebuild tests require PyYAML from the locked Harbor runtime')
class DifficultyAuditTest(unittest.TestCase):
    def test_source_reconstruction_preserves_deletions_and_discards_generated_manifest(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        tmp_path = Path(holder.name)
        extraction = captured(tmp_path, {'dbt_project.yml': 'name: example\ntarget-path: generated\n',
            'models/new.sql': 'select 2 as id', 'generated/manifest.json': '{"fake": true}',
            'target/run_results.json': '{"status": "success"}', '.qwen/settings.json': '{}'})
        baseline = {'initial_trees': [{'relative_path': 'models', 'files': [
            {'path': 'old.sql', 'sha256': 'old-bytes'}]}], 'initial_files': []}
        submission, names, plan = source_plan(extraction, baseline, {'fresh_artifact_paths': ['phase3_target']})
        assert submission['delete_paths'] == ['models/old.sql']
        assert {x['path'] for x in submission['files']} == {'dbt_project.yml', 'models/new.sql'}
        assert names == ['dbt_project.yml', 'models/new.sql']
        assert 'generated' in plan['generated_dirs_discarded']


    def test_missing_delivered_project_is_not_repaired_from_baseline(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        tmp_path = Path(holder.name)
        extraction = captured(tmp_path, {'models/new.sql': 'select 1'})
        with self.assertRaisesRegex(ValueError, 'no dbt_project.yml'):
            source_plan(extraction, {'initial_trees': [], 'initial_files': []}, {})


    def test_fresh_build_cannot_substitute_reference_sql(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        tmp_path = Path(holder.name)
        extraction = captured(tmp_path, {'models/answer.sql': 'select 42 as value'})
        row = {'relative_path': 'models/answer.sql', 'exists': True,
               'sha256': extraction['source_files'][0]['sha256']}
        execution = {'submitted_files': [row], 'final_files': [deepcopy(row)]}
        assert_delivered_sources(execution, ['models/answer.sql'], extraction)
        execution['final_files'][0]['sha256'] = hashlib.sha256(b'select 43 as value').hexdigest()
        with self.assertRaisesRegex(ValueError, 'preserve delivered source'):
            assert_delivered_sources(execution, ['models/answer.sql'], extraction)


    def test_successful_manifest_does_not_excuse_missing_delivered_source(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        tmp_path = Path(holder.name)
        extraction = captured(tmp_path, {'models/answer.sql': 'select 42 as value'})
        execution = {'submitted_files': [], 'final_files': []}
        with self.assertRaisesRegex(ValueError, 'preserve delivered source'):
            assert_delivered_sources(execution, ['models/answer.sql'], extraction)
