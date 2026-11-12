#!/usr/bin/env python3
"""Derive one mechanical base from a verified transport-only image layer."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
import tomllib
from dbt_bench import io
from dbt_bench.prepare import check_harbor
from review_tools import ROOT


def derive(base, task_key, layer_path, output):
    base, layer_path, output = map(Path, (base, layer_path, output))
    if output.exists() or not str(output.resolve()).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH base')
    manifest = io.read_json(base / 'manifest.json')
    old = manifest['tasks'][task_key]
    layer = io.read_json(layer_path)
    for flag in ('passed', 'task_files_exact', 'task_packages_exact', 'task_python_exact'):
        if layer.get(flag) is not True:
            raise ValueError('Unverified environment transport layer')
    for ref in [v for v in layer.values() if isinstance(v, dict) and 'sha256' in v]:
        if not io.evidence_ok(ref, ROOT):
            raise ValueError('Transport-layer evidence changed')
    if layer['before_image']['sha256'] != old['backend_binding']['image_sha256']:
        raise ValueError('Layer applies to another base image')
    if io.tree_hash(base / 'tasks' / task_key) != old['validation_copy_hash']:
        raise ValueError('Mechanical source changed')
    output.mkdir(parents=True)
    shutil.copytree(base / 'tools', output / 'tools')
    target = output / 'tasks' / task_key
    shutil.copytree(base / 'tasks' / task_key, target)
    config = io.read_json(base / 'config.yaml')
    before = (target / 'task.toml').read_text()
    image = tomllib.loads(before)['environment']['docker_image']
    if before.count(image) != 1:
        raise ValueError('Ambiguous image locator')
    after_image = str((ROOT / layer['after_image']['path']).resolve())
    (target / 'task.toml').write_text(before.replace(image, after_image))
    entry = deepcopy(old)
    binding = {**old['backend_binding'], 'image_sha256': layer['after_image']['sha256'],
               'task_sha256': io.tree_hash(target), 'evidence_current': True}
    extension = {'summary': io.evidence_ref(layer_path, ROOT), 'before_binding': old['backend_binding']}
    binding['binding_sha256'] = io.digest({'extension': extension, 'image': layer['after_image'], 'task': binding['task_sha256']})
    entry.update(backend_binding=binding, environment_extension=extension, validation_copy_hash=io.tree_hash(target),
                 native_verifier=io.evidence_ref(target / 'tests/mechanical-native-test.sh', ROOT))
    if entry['native_verifier']['sha256'] != old['native_verifier']['sha256']:
        raise ValueError('Verifier changed')
    config.update(job_name=output.name, jobs_dir=str(output / 'jobs'), tasks=[{'path': str(target)}])
    io.write_json(output / 'config.yaml', check_harbor(config, [target]))
    io.write_json(output / 'manifest.json', {**manifest, 'tasks': {task_key: entry}, 'planned_trials': 2,
        'config': io.evidence_ref(output / 'config.yaml', ROOT),
        'tools': [io.evidence_ref(p, ROOT) for p in (output / 'tools').glob('*.py')],
        'previous_base': io.evidence_ref(base / 'manifest.json', ROOT),
        'environment_extension': extension, 'old_bindings_modified': False})
    return {'base': str(output), 'task_key': task_key}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', required=True, type=Path)
    p.add_argument('--task', required=True)
    p.add_argument('--layer', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    print(derive(a.base, a.task, a.layer, a.output))
