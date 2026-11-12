"""Prepare source-specific actual native14 probes as ordinary Harbor jobs."""
from dbt_bench.local_config import expand as _release_expand
import argparse
import importlib
from pathlib import Path
import shutil
import sys
from review_tools import ROOT, cached_hashes
from dbt_bench import io
from dbt_bench.backends import resolve_task, binding_identity, configure_backend
from dbt_bench.catalog import load_catalog
from dbt_bench.prepare import check_harbor
from visibility_scan import fingerprint
SCRIPTS = ('native_mechanical_agent.py', 'mechanical_observer.py', 'context_probe.py', 'visibility_scan.py')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', type=Path, action='append', required=True, help='One task spec or keyed spec map')
    p.add_argument('--source', choices=('ade', 'spider2', 'deb'), required=True)
    p.add_argument('--task', action='append')
    p.add_argument('--output', type=Path, required=True, help='New physical SCRATCH directory')
    p.add_argument('--concurrent', type=int, default=4)
    p.add_argument('--ade-build-in-qwen', action='store_true', help='Also execute the native ADE test-setup build step through Qwen before grading')
    a = p.parse_args()
    if a.output.exists() or a.concurrent < 1:
        p.error('New output directory and positive concurrency required')
    if not str(a.output.resolve()).startswith('/scratch/'):
        p.error('Bulk outputs must live on SCRATCH')
    specs, origins = ({}, {})
    for source in a.spec:
        value = io.read_json(source)
        entries = {s['task_key']: s for s in value} if isinstance(value, list) else {value['task_key']: value} if 'task_key' in value else value
        for key, spec in entries.items():
            if a.task and key not in a.task:
                continue
            assert key not in specs, 'Duplicate specification: ' + key
            specs[key], origins[key] = (spec, str(source))
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    broken = {r['task_key'] for r in io.read_jsonl(ROOT / 'validation/ledger.jsonl') if r['native']['conclusion'] == 'broken'}
    assert specs and (not specs.keys() & broken)
    assert not a.task or set(a.task) == set(specs)
    assert all((rows[k]['source'] == a.source for k in specs))
    assert not a.ade_build_in_qwen or a.source == 'ade'
    normalize = importlib.import_module('mechanical_sources.' + ('spider' if a.source == 'spider2' else a.source)).normalize
    a.output.mkdir(parents=True)
    (a.output / 'tools').mkdir()
    for name in SCRIPTS:
        shutil.copyfile(ROOT / 'scripts' / name, a.output / 'tools' / name)
    config = io.read_json(ROOT / 'configs/validation.yaml')
    configure_backend(config, 'apptainer')
    bundle = ROOT / '.cache/qwen-runtime/locked-v1/bundle.json'
    config.update(job_name=a.output.name, jobs_dir=str(a.output / 'jobs'), n_attempts=2, n_concurrent_trials=a.concurrent, agents=[{'import_path': 'native_mechanical_agent:NativeMechanicalAgent', 'env': {'DB_TYPE': 'duckdb'}, 'kwargs': {'installation_bundle': str(bundle.resolve()), 'installation_bundle_sha256': io.file_hash(bundle)}}], tasks=[])
    entries = {}
    with cached_hashes():
        for key, raw in specs.items():
            row = rows[key]
            native = ROOT / row['run_path']
            bound = resolve_task(ROOT, row, 'apptainer')
            task = a.output / 'tasks' / key
            shutil.copytree(bound, task, symlinks=True)
            spec = normalize(raw)
            assert spec['task_key'] == key and spec['outputs']
            assert spec['reference_kind'] in {'built_from_inputs', 'copy_gold', 'unknown'}
            assert spec.get('reference_command', 'bash /solution/solve.sh') == 'bash /solution/solve.sh', 'Alternate witness must be separately prepared and pinned'
            for output in spec['outputs']:
                assert output['database'].startswith('/') and isinstance(output['relation'], str)
                assert output.get('columns') is None or all((isinstance(c, str) for c in output['columns']))
            payload = task / 'mechanical'
            payload.mkdir()
            io.write_json(task / 'reviewed-source-spec.json', raw)
            runtime_spec = {k: spec[k] for k in ('task_key', 'source', 'database_paths', 'outputs', 'files', 'reference_kind', 'hidden_relations') if k in spec}
            runtime_spec['outputs'] = [{k: o[k] for k in ('database', 'relation', 'columns', 'keys', 'ordered', 'tolerances', 'dynamic_columns', 'capture_scope', 'required') if k in o} for o in spec['outputs']]
            io.write_json(payload / 'spec.json', runtime_spec)
            if a.ade_build_in_qwen:
                build = native / 'tests/test-setup.sh'
                assert build.is_file()
                for line in build.read_text().splitlines():
                    if line.strip() and (not line.lstrip().startswith('#')):
                        assert line.strip().startswith(('dbt run', 'dbt deps')), 'Review non-build setup command before moving it'
                (task / 'reference-build').mkdir()
                shutil.copyfile(build, task / 'reference-build/build.sh')
            for name in SCRIPTS[1:]:
                shutil.copyfile(a.output / 'tools' / name, payload / name)
            hidden_paths = set()
            for directory in (native / 'tests', native / 'solution'):
                hidden_paths.update((f for f in directory.rglob('*') if f.is_file() and f.stat().st_size))
            for item in spec.get('hidden_sources', []):
                candidate = ROOT / item
                if not candidate.exists():
                    candidate = native / item
                assert candidate.exists(), 'Missing declared hidden source: ' + item
                hidden_paths.update([candidate] if candidate.is_file() else [f for f in candidate.rglob('*') if f.is_file() and f.stat().st_size])
            hidden = []
            for path in sorted(hidden_paths):
                size = path.stat().st_size
                if size <= 2 * 1024 * 1024:
                    hidden.append(fingerprint(str(path.relative_to(ROOT)), path.read_bytes()))
                else:
                    hidden.append({'source': str(path.relative_to(ROOT)), 'size': size, 'sha256': io.file_hash(path), 'normalized_lines': None})
            visibility = {'hidden_assets': hidden, 'excluded_roots': ['/proc', '/sys', '/dev', '/tmp/dbt-bench-mechanical', '/logs', '/staging'], 'excluded_files': [], 'structured_artifacts': [], 'databases': [], 'declared_absent_paths': ['/solution', '/tests'], 'host_paths': [str(ROOT), str(ROOT.parent / 'training'), _release_expand('${HOME}')], 'maximum_text_bytes': 2 * 1024 * 1024, 'scope': 'Actual initial filesystem exact/normalized hidden-asset scan, before any solution/verifier upload. Exclusions are synthetic/kernel/runtime instrumentation only.'}
            for item in spec.get('hidden_relations', []):
                if isinstance(item, dict) and 'path' in item and ('absent_relations' in item):
                    visibility['databases'].append(item)
            io.write_json(payload / 'visibility.json', visibility)
            test = task / 'tests/test.sh'
            original = test.read_bytes()
            assert not (test.parent / 'mechanical-native-test.sh').exists()
            test.rename(test.parent / 'mechanical-native-test.sh')
            observer = 'python3 /tmp/dbt-bench-mechanical/mechanical_observer.py --spec /tmp/dbt-bench-mechanical/spec.json'
            test.write_text('#!/bin/bash\n' + observer + ' --stage verifier-before --output /logs/verifier/mechanical-before\nbash /tests/mechanical-native-test.sh\nrc=$?\n' + observer + ' --stage verifier-after --output /logs/verifier/mechanical-after\nexit "$rc"\n')
            assert (test.parent / 'mechanical-native-test.sh').read_bytes() == original == (native / 'tests/test.sh').read_bytes()
            config['tasks'].append({'path': str(task)})
            entries[key] = {'specification_source': io.evidence_ref(task / 'reviewed-source-spec.json', ROOT), 'source_specification_locator': origins[key], 'normalized_specification': io.evidence_ref(payload / 'spec.json', ROOT), 'visibility_specification': io.evidence_ref(payload / 'visibility.json', ROOT), 'original_task_hash': row['adapted_task_hash'], 'backend_binding': binding_identity(ROOT, row, 'apptainer'), 'validation_copy_hash': io.tree_hash(task), 'native_verifier': io.evidence_ref(test.parent / 'mechanical-native-test.sh', ROOT), 'wrapper': io.evidence_ref(test, ROOT), 'reference_kind': spec['reference_kind']}
            if a.ade_build_in_qwen:
                entries[key]['additional_qwen_build'] = io.evidence_ref(task / 'reference-build/build.sh', ROOT)
            print('prepared', key, flush=True)
    config = check_harbor(config, [Path(t['path']) for t in config['tasks']])
    io.write_json(a.output / 'config.yaml', config)
    io.write_json(a.output / 'manifest.json', {'schema_version': 1, 'source': a.source, 'tasks': entries, 'planned_trials': 2 * len(entries), 'config': io.evidence_ref(a.output / 'config.yaml', ROOT), 'source_lock': io.evidence_ref(ROOT / 'sources.lock.yaml', ROOT), 'runtime_lock': io.evidence_ref(ROOT / 'runtime/uv.lock', ROOT), 'installation_bundle': io.evidence_ref(bundle, ROOT), 'model_requests': 0, 'tools': [io.evidence_ref(f, ROOT) for f in sorted((a.output / 'tools').iterdir())], 'scope': 'Actual locked Qwen shell-service reference execution plus unchanged native verifier in isolated Harbor task copies. Native copy-gold observations never certify workflow solvability.'})
    print({'tasks': len(entries), 'trials': 2 * len(entries), 'config': str(a.output / 'config.yaml')})
if __name__ == '__main__':
    main()
