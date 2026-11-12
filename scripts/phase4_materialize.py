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
            if row['split'] != 'test' or row['source'] != 'deb':
                raise ValueError('Only authorized DEB test variants are allowed')
            source = resolve_task(ROOT, row, 'apptainer')
            selected = item['selected_inputs']['instruction_md']
            if not io.evidence_ok(selected, ROOT):
                raise ValueError('Variant instruction changed')
            version = 'phase4-instruction-' + selected['sha256'][:16]
            target = output / 'tasks' / key / version
            shutil.copytree(source, target)
            before = (target / 'instruction.md').read_text()
            shutil.copyfile(ROOT / selected['path'], target / 'instruction.md')
            validate_layout(target)
            if any((target / p).exists() for p in ('audit-rebuild', 'mechanical', 'mechanical-case', 'phase3-case', 'phase4-case')):
                raise ValueError('Validation-only payload must not enter production task')
            if io.tree_hash(target / 'tests') != io.tree_hash(source / 'tests'):
                raise ValueError('Native tests changed')
            patch = ROOT / 'patches' / ('phase4-' + key + '-' + selected['sha256'][:12])
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
                'validation_status': 'See separately versioned phase 4 companion ledger; materialization is not certification',
                'model_requests': 0}
            io.write_json(patch / 'metadata.json', metadata)
            variants[key] = {'task_version': version, 'run_path': str(target),
                             'metadata': io.evidence_ref(patch / 'metadata.json', ROOT)}
    manifest = {'schema_version': 1, 'model_requests': 0,
        'inventory': io.evidence_ref(inventory_path, ROOT), 'variants': variants,
        'scope': 'Runnable native task variants; no agent source, hidden case payload or observation wrapper added.'}
    io.write_json(output / 'manifest.json', manifest)
    io.write_json(ROOT / 'catalog/phase4-task-versions.json', manifest)
    return {'task_versions': len(variants), 'manifest': str(output / 'manifest.json')}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(materialize(a.inventory, a.output))
