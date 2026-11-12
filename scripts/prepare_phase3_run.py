#!/usr/bin/env python3
"""Prepare a frozen train-pool Harbor configuration without starting a model."""
import argparse
from copy import deepcopy
from pathlib import Path
import re
import shutil
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from dbt_bench.prepare import emit_config, check_harbor
from prepare_model_run import finalize_run
from review_tools import cached_hashes


def selected_tasks(root, pointer, source, keys):
    """Select only hash-bound eligible train rows from an immutable freeze."""
    root, pointer = Path(root), Path(pointer)
    selection = io.read_json(pointer)['selection']
    if not io.evidence_ok(selection, root):
        raise ValueError('Frozen selection changed')
    manifest = io.read_json(root / selection['path'])
    if not io.evidence_ok(manifest['candidates'], root):
        raise ValueError('Frozen candidates changed')
    rows = io.read_jsonl(root / manifest['candidates']['path'])
    indexed = {r['task_key']: r for r in rows}
    if len(indexed) != len(rows) or not keys or len(keys) != len(set(keys)):
        raise ValueError('Require distinct explicit task IDs and unique catalog records')
    selected = []
    for key in keys:
        row = indexed.get(key)
        if not row or row['source'] != source or row['split'] != 'train' or row['eligible_for_sampling'] is not True:
            raise ValueError('Task is not an eligible train member of this source/freeze: ' + key)
        if row.get('phase3_record'):
            if not io.evidence_ok(row['phase3_record'], root):
                raise ValueError('Frozen task review changed')
            review = io.read_json(root / row['phase3_record']['path'])
            if (review['task_key'] != key or not review['eligible_for_sampling']
                    or review['task_version'] != row['task_version']):
                raise ValueError('Candidate differs from reviewed task')
        selected.append(row)
    return selected, selection


def production_task(root, row, base_path, base_image_sha):
    root, base_path = Path(root), Path(base_path)
    path, metadata_ref = base_path, row.get('task_version_metadata')
    if row.get('run_path'):
        if not metadata_ref or not io.evidence_ok(metadata_ref, root):
            raise ValueError('Versioned production task lacks intact metadata')
        metadata = io.read_json(root / metadata_ref['path'])
        path = root / row['run_path']
        if (metadata['task_key'] != row['task_key'] or metadata['task_version'] != row['task_version']
                or metadata['native_verifier_modified'] or io.tree_hash(path) != metadata['production_task_hash']):
            raise ValueError('Production task differs from reviewed version')
        if io.tree_hash(path / 'tests') != io.tree_hash(base_path / 'tests'):
            raise ValueError('Versioned task changed native tests')
    image = root / tomllib.loads((path / 'task.toml').read_text())['environment']['docker_image']
    expected_image = base_image_sha
    if row.get('phase3_record'):
        record = io.read_json(root / row['phase3_record']['path'])
        identity = record['phase3_identity']
        expected_image = identity.get('actual_backend_binding', identity['base_identity']['backend_binding'])['image_sha256']
        if io.file_hash(path / 'instruction.md') != identity['instruction_sha256']:
            raise ValueError('Instruction differs from frozen reviewed version')
    if io.file_hash(image) != expected_image:
        raise ValueError('Production image differs from frozen review')
    # A formal validation task must never be used as a production task.
    if any((path / name).exists() for name in ('audit-rebuild', 'phase3-case', 'mechanical', 'mechanical-case')):
        raise ValueError('Validation-only payload present in production task')
    return path, {'metadata': metadata_ref, 'image': io.evidence_ref(image, root),
                  'task_hash': io.tree_hash(path), 'source_path': str(path)}


def prepare(root, output, *, pointer, source, tasks, model, endpoint, deployment, attempts=1, concurrent=1):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or not str(output).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH run directory')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', output.name):
        raise ValueError('Use a simple explicit run name')
    rows, selection = selected_tasks(root, pointer, source, tasks)
    output.mkdir(parents=True)
    builder = output / 'phase3-builder.py'
    shutil.copyfile(__file__, builder)
    with cached_hashes():
        base = emit_config(root, source, model, endpoint, deployment, output=output / 'original-config.yaml',
                           tasks=tasks, backend='apptainer', attempts=attempts, concurrent=concurrent)
        original = io.read_json(base.with_suffix('.manifest.json'))
        config = io.read_json(base)
        identity = deepcopy(original['identity'])
        originals = {Path(t['path']).resolve().name: Path(t['path']) for t in config['tasks']}
        production = {}
        for row in rows:
            key = row['task_key']
            path, facts = production_task(root, row, originals[key], identity['backend_bindings'][key]['image_sha256'])
            destination = output / 'tasks' / key
            shutil.copytree(path, destination)
            if io.tree_hash(destination) != facts['task_hash']:
                raise ValueError('Production copy differs')
            production[key] = {**facts, 'run_path': str(destination), 'pool_row': row}
        config['tasks'] = [{'path': production[row['task_key']]['run_path']} for row in rows]
        config = check_harbor(config, [Path(t['path']) for t in config['tasks']])
        from harbor.models.task.task import Task
        names = [Task(Path(t['path'])).name for t in config['tasks']]
        if len(names) != len(set(names)):
            raise ValueError('Ambiguous native Harbor task names')
        # DEB declares its own names in [task]. Preserve those native names and
        # bind them explicitly to catalog IDs instead of editing task content.
        identity['harbor_task_names'] = dict(zip(names, [r['task_key'] for r in rows]))
        identity.update(config=config, phase3_selection=selection, phase3_production=production,
                        phase3_configuration_builder=io.evidence_ref(builder, root))
        identity['base_backend_bindings'] = identity['backend_bindings']
        identity['backend_bindings'] = {
            key: {'image_sha256': facts['image']['sha256'], 'task_sha256': facts['task_hash'],
                  'binding_sha256': io.digest({'selection': selection, 'production': facts}),
                  'evidence_current': True, 'scope': 'Actual selected production task/image; original binding retained as base_backend_bindings'}
            for key, facts in production.items()}
        identity['tasks'] = {key: row['task_hash'] for key, row in production.items()}
        resolved = output / 'selected-config.yaml'
        io.write_json(resolved, config)
        io.write_json(resolved.with_suffix('.manifest.json'), {'identity': identity, 'experiment_id': io.digest(identity),
            'config_hash': io.file_hash(resolved), 'runtime_schema_checked': True, 'planned': original['planned']})
        result = finalize_run(root, output, resolved)
    return {'config': str(output / 'config.yaml'), 'manifest': str(output / 'manifest.json'),
            'experiment_id': result['experiment_id'], 'tasks': len(rows), 'model_requests': 0,
            'scope': 'Prepared only; model context capture and output audit must still be assessed after actual rollout.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool', type=Path, default=ROOT / 'catalog/phase3-current.json')
    p.add_argument('--source', choices=['ade', 'spider2', 'deb'], required=True)
    p.add_argument('--task', action='append', required=True)
    p.add_argument('--model', required=True); p.add_argument('--endpoint', required=True); p.add_argument('--deployment', required=True)
    p.add_argument('--attempts', type=int, default=1); p.add_argument('--concurrent', type=int, default=1)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    print(prepare(ROOT, a.output_dir, pointer=a.pool, source=a.source, tasks=a.task, model=a.model,
                  endpoint=a.endpoint, deployment=a.deployment, attempts=a.attempts, concurrent=a.concurrent))
