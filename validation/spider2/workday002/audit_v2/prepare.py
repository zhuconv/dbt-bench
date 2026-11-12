"""Prepare isolated native oracle/nop controls or true dbt reference captures."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from install_capture import install
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(HERE.parent / 'workflow_v2'))
from task_copy import copy_task
from dbt_bench.backends import binding_identity, configure_backend, resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, read_json, tree_hash, write_json
from dbt_bench.prepare import check_harbor


def prepare(args):
    out = args.output_dir.resolve()
    row = next(r for r in load_catalog(ROOT) if r['task_key'] == 'spider2__workday002')
    native = resolve_task(ROOT, row, 'apptainer')
    if args.kind == 'reference':
        subprocess.run([sys.executable, str(HERE.parent / 'workflow_v2/prepare.py'),
            '--case', 'original-reference', '--output-dir', str(out), '--concurrent', '2'], check=True)
        config, manifest = read_json(out / 'config.yaml'), read_json(out / 'manifest.json')
    else:
        out.mkdir(parents=True, exist_ok=False)
        copied = out / 'tasks' / row['task_key'];copy_task(native, copied)
        config = read_json(ROOT / 'configs/validation.yaml');configure_backend(config, 'apptainer')
        config.update(job_name=out.name, jobs_dir=str(out / 'jobs'), n_attempts=2,
                      n_concurrent_trials=2, tasks=[{'path': str(copied)}])
        assert {a['name'] for a in config['agents']} == {'oracle', 'nop'} and config['retry']['max_retries'] == 0
        manifest = {'schema_version': 1, 'kind': 'native_reference_and_nop_validation',
            'backend': 'apptainer', 'baseline': False, 'qwen_smoke': False,
            'planned': [{'task_key': row['task_key'], 'agent': agent, 'attempt': attempt}
                        for agent in ['oracle', 'nop'] for attempt in [1, 2]],
            'tasks': {row['task_key']: {'original_task_hash': row['adapted_task_hash'],
                'backend_binding': binding_identity(ROOT, row, 'apptainer')}},
            'source_lock': evidence_ref(ROOT / 'sources.lock.yaml', ROOT),
            'runtime_lock': evidence_ref(ROOT / 'runtime/uv.lock', ROOT)}
    changes = {}
    for task in config['tasks']:
        path = Path(task['path']);before = tree_hash(path)
        changes[path.name] = {**install(path), 'before_task_hash': before, 'after_task_hash': tree_hash(path)}
        if args.kind == 'reference':
            entry = next(p for p in manifest['planned'] if p['task_label'] == path.name)
            entry['validation_copy_hash'] = tree_hash(path)
        else:
            manifest['tasks'][path.name]['validation_copy_hash'] = tree_hash(path)
    config = check_harbor(config, [Path(t['path']) for t in config['tasks']])
    write_json(out / 'config.yaml', config)
    manifest.update(capture_kind=args.kind, read_only_capture=changes,
        config=evidence_ref(out / 'config.yaml', ROOT), collector=evidence_ref(HERE / 'collect.py', ROOT),
        capture_installer=evidence_ref(HERE / 'install_capture.py', ROOT),
        capture_preparation=evidence_ref(Path(__file__), ROOT),
        scope='Native scores untouched; oracle copy-gold is not a build witness. Additional before/after exports are read-only.')
    write_json(out / 'manifest.json', manifest)
    print({'kind': args.kind, 'planned': len(manifest['planned']), 'config': str(out / 'config.yaml')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=['controls', 'reference'], required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    prepare(parser.parse_args())
