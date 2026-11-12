#!/usr/bin/env python3
"""Prepare exact phase3/phase4 task versions for difficulty measurement only."""
from copy import deepcopy
from pathlib import Path
import shutil
import tomllib

from dbt_bench import io
from dbt_bench.prepare import emit_config, check_harbor
from prepare_model_run import finalize_run
from review_tools import cached_hashes


def select(root, pool_path, source, key):
    root, pool_path = Path(root), Path(pool_path)
    pool = io.read_json(pool_path)
    rules_ref = io.evidence_ref(root / 'configs/difficulty-v1/measurement-rules.json', root)
    if not pool['phase4_review_complete'] or not pool['training_prohibited'] or pool['measurement_rules'] != rules_ref:
        raise ValueError('Use the completed, measurement-only phase3+phase4 population')
    for name in ('phase3_pool', 'phase4_pool', 'split_rules'):
        if not io.evidence_ok(pool[name], root):
            raise ValueError('Population inputs changed')
    indexed = io.unique_index(pool['tasks'], 'task_key')
    selected = indexed[key]
    if not selected['eligible_for_measurement'] or selected['source'] != source:
        raise ValueError('Task is not eligible for this measurement source')
    review_ref = selected['qualified_review']
    if not io.evidence_ok(review_ref, root):
        raise ValueError('Qualified review changed')
    review = io.read_json(root / review_ref['path'])
    if (review['task_key'] != key or review['task_version'] != selected['task_version']
            or not review['eligible_for_sampling'] or review['audit']['conclusion'] != 'validated'
            or review['native']['conclusion'] == 'broken'):
        raise ValueError('Measurement candidate differs from its qualified review')
    return selected, review


def production_task(root, selected, review, base_path):
    root, base_path = Path(root), Path(base_path)
    row = selected['frozen_pool_row']
    phase = selected['qualification_phase']
    identity = review[f'phase{phase}_identity']
    path = root / row['run_path'] if row.get('run_path') else base_path
    metadata_ref = row.get('task_version_metadata')
    if metadata_ref:
        if not io.evidence_ok(metadata_ref, root):
            raise ValueError('Variant metadata changed')
        metadata = io.read_json(root / metadata_ref['path'])
        if (metadata['task_key'] != selected['task_key'] or metadata['task_version'] != selected['task_version']
                or metadata['native_verifier_modified'] or io.tree_hash(path) != metadata['production_task_hash']):
            raise ValueError('Versioned production task differs from reviewed bytes')
    elif io.tree_hash(path) != io.tree_hash(base_path):
        raise ValueError('Unregistered production task change')
    if io.tree_hash(path / 'tests') != io.tree_hash(base_path / 'tests'):
        raise ValueError('Native verifier changed')
    if io.file_hash(path / 'instruction.md') != identity['instruction_sha256']:
        raise ValueError('Wrong instruction version')
    image = root / tomllib.loads((path / 'task.toml').read_text())['environment']['docker_image']
    binding = identity.get('actual_backend_binding', identity['base_identity']['backend_binding'])
    if io.file_hash(image) != binding['image_sha256']:
        raise ValueError('Wrong reviewed container image')
    if any((path / name).exists() for name in ('audit-rebuild', 'phase3-case', 'phase4-case', 'mechanical', 'mechanical-case')):
        raise ValueError('Validation payload must not enter production agent tasks')
    return path, {'metadata': metadata_ref, 'image': io.evidence_ref(image, root),
        'task_hash': io.tree_hash(path), 'source_path': str(path), 'qualified_review': selected['qualified_review'],
        'qualification_phase': phase, 'pool_row': row}


def prepare(root, output, *, pool, source, key, model, endpoint, deployment, attempts):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or not str(output).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH directory')
    selected, review = select(root, pool, source, key)
    output.mkdir(parents=True)
    builder = output / 'measured-builder.py'; shutil.copyfile(__file__, builder)
    with cached_hashes():
        original_path = emit_config(root, source, model, endpoint, deployment,
            output=output / 'original-config.yaml', tasks=[key], backend='apptainer', attempts=attempts, concurrent=1)
        original = io.read_json(original_path.with_suffix('.manifest.json'))
        config = io.read_json(original_path)
        identity = deepcopy(original['identity'])
        base = Path(config['tasks'][0]['path'])
        path, facts = production_task(root, selected, review, base)
        task = output / 'tasks' / key; shutil.copytree(path, task)
        if io.tree_hash(task) != facts['task_hash']:
            raise ValueError('Production copy differs')
        config['tasks'] = [{'path': str(task)}]
        config = check_harbor(config, [task])
        from harbor.models.task.task import Task
        production = {key: {**facts, 'run_path': str(task)}}
        selection = io.evidence_ref(pool, root)
        identity.update(config=config, measurement_population=selection, reviewed_production=production,
            measured_configuration_builder=io.evidence_ref(builder, root),
            harbor_task_names={Task(task).name: key}, training_prohibited=True)
        identity['base_backend_bindings'] = identity['backend_bindings']
        identity['backend_bindings'] = {key: {'image_sha256': facts['image']['sha256'],
            'task_sha256': facts['task_hash'], 'binding_sha256': io.digest({'population': selection, 'production': facts}),
            'evidence_current': True, 'scope': 'Actual difficulty-only production task version'}}
        identity['tasks'] = {key: facts['task_hash']}
        chosen = output / 'selected-config.yaml'; io.write_json(chosen, config)
        io.write_json(chosen.with_suffix('.manifest.json'), {'identity': identity,
            'experiment_id': io.digest(identity), 'config_hash': io.file_hash(chosen),
            'runtime_schema_checked': True, 'planned': original['planned']})
        result = finalize_run(root, output, chosen)
    return result
