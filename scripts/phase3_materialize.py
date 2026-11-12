#!/usr/bin/env python3
"""Materialize production task variants with native tests and no validation payload."""
import argparse
import difflib
from pathlib import Path
import shutil
import tomllib
from dbt_bench import io
from dbt_bench.backends import resolve_task
from dbt_bench.prepare import validate_layout
from review_tools import ROOT, cached_hashes


def materialize_environment(task_key, layer_path, output):
    """Keep a transport-only environment version separate from instruction variants."""
    output = Path(output)
    if output.exists() or not str(output.resolve()).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH directory')
    row = io.unique_index(io.read_jsonl(ROOT / 'catalog/tasks.jsonl'), 'task_key')[task_key]
    if row['split'] != 'train':
        raise ValueError('Only train environment versions are allowed')
    layer = io.read_json(layer_path)
    if not all(layer.get(k) is True for k in ('passed', 'task_files_exact', 'task_python_exact', 'task_packages_exact')):
        raise ValueError('Environment layer has no compatible task-data proof')
    if not io.evidence_ok(layer['after_image'], ROOT):
        raise ValueError('Environment image changed')
    source = resolve_task(ROOT, row, 'apptainer')
    before = (source / 'task.toml').read_text()
    old_image = tomllib.loads(before)['environment']['docker_image']
    if io.file_hash(old_image) != layer['before_image']['sha256'] or before.count(old_image) != 1:
        raise ValueError('Transport layer does not match production source')
    shutil.copytree(source, output)
    (output / 'task.toml').write_text(before.replace(old_image, str((ROOT / layer['after_image']['path']).resolve())))
    validate_layout(output)
    if io.tree_hash(source / 'tests') != io.tree_hash(output / 'tests'):
        raise ValueError('Verifier changed')
    metadata = {'task_key': task_key, 'task_version': 'original-' + row['adapted_task_hash'][:16],
        'run_path': str(output), 'production_task_hash': io.tree_hash(output),
        'type': 'environment_transport_only', 'layer': io.evidence_ref(layer_path, ROOT),
        'instruction_unchanged': True, 'native_verifier_modified': False}
    path = output.parent / (output.name + '-metadata.json')
    io.write_json(path, metadata)
    manifest = {'schema_version': 1, 'environments': {task_key: {**metadata, 'metadata': io.evidence_ref(path, ROOT)}}}
    io.write_json(ROOT / 'catalog/phase3-environment-versions.json', manifest)
    return metadata


def materialize(inventory_path, output):
    output = Path(output)
    if output.exists() or not str(output.resolve()).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH directory')
    inventory = io.read_json(inventory_path)
    rows = io.unique_index(io.read_jsonl(ROOT / 'catalog/tasks.jsonl'), 'task_key')
    output.mkdir(parents=True)
    variants = {}
    with cached_hashes():
        for item in inventory['tasks']:
            if not item.get('instruction_variant'):
                continue
            key = item['task_key']; row = rows[key]
            if row['split'] != 'train':
                raise ValueError('No test variants allowed')
            source = resolve_task(ROOT, row, 'apptainer')
            selected = item['selected_inputs']['instruction_md']
            if not io.evidence_ok(selected, ROOT):
                raise ValueError('Variant instruction changed')
            version = 'phase3-instruction-' + selected['sha256'][:16]
            target = output / 'tasks' / key / version
            shutil.copytree(source, target)
            before = (target / 'instruction.md').read_text()
            shutil.copyfile(ROOT / selected['path'], target / 'instruction.md')
            validate_layout(target)
            if any((target / p).exists() for p in ('audit-rebuild', 'mechanical', 'mechanical-case')):
                raise ValueError('Validation-only payload must not enter production task')
            if io.tree_hash(target / 'tests') != io.tree_hash(source / 'tests'):
                raise ValueError('Native tests changed')
            patch = ROOT / 'patches' / ('phase3-' + key + '-' + selected['sha256'][:12])
            patch.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(target / 'instruction.md', patch / 'instruction.md')
            (patch / 'instruction.diff').write_text(''.join(difflib.unified_diff(before.splitlines(True),
                (target / 'instruction.md').read_text().splitlines(True),
                fromfile='original/instruction.md', tofile='variant/instruction.md')))
            metadata = {'schema_version': 1, 'task_key': key, 'task_version': version,
                'type': 'task_instruction_variant', 'gold_derived_correction': item['gold_derived_correction'],
                'source_task_hash': row['adapted_task_hash'], 'source_backend_task_hash': io.tree_hash(source),
                'selected_instruction': selected, 'patch': io.evidence_ref(patch / 'instruction.diff', ROOT),
                'instruction_before': io.evidence_ref(source / 'instruction.md', ROOT),
                'instruction_after': io.evidence_ref(target / 'instruction.md', ROOT),
                'production_task': str(target), 'production_task_hash': io.tree_hash(target),
                'native_tests_hash': io.tree_hash(target / 'tests'), 'native_verifier_modified': False,
                'validation_status': 'See separately versioned phase 3 companion ledger; materialization is not certification',
                'model_requests': 0}
            io.write_json(patch / 'metadata.json', metadata)
            variants[key] = {'task_version': version, 'run_path': str(target),
                             'metadata': io.evidence_ref(patch / 'metadata.json', ROOT)}
    manifest = {'schema_version': 1, 'model_requests': 0,
        'inventory': io.evidence_ref(inventory_path, ROOT), 'variants': variants,
        'scope': 'Runnable native task variants; no agent source, hidden case payload or observation wrapper added.'}
    io.write_json(output / 'manifest.json', manifest)
    io.write_json(ROOT / 'catalog/phase3-task-versions.json', manifest)
    return {'task_versions': len(variants), 'manifest': str(output / 'manifest.json')}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(materialize(a.inventory, a.output))
