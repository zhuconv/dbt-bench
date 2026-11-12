#!/usr/bin/env python3
"""Pin user-supplied public SQL leads in clean no-model Harbor validation copies."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
from dbt_bench import io
from dbt_bench.backends import binding_identity
from dbt_bench.catalog import load_catalog
from dbt_bench.prepare import check_harbor
from review_tools import ROOT, cached_hashes


def candidate_files(directory, metadata):
    files = []
    for item in metadata['files']:
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'models':
            raise ValueError('Only explicit relative model paths allowed')
        source = directory / 'files' / relative
        if io.file_hash(source) != item['sha256']:
            raise ValueError('Candidate SQL hash mismatch: ' + str(source))
        files.append((relative.as_posix(), source, item['sha256']))
    if len({name for name, _, _ in files}) != len(files):
        raise ValueError('Duplicate candidate path')
    modified = directory / 'dbt_project.yml.modified'
    if bool(metadata['project_yml_changed']) != modified.exists():
        raise ValueError('Project modification metadata mismatch')
    if modified.exists():
        files.append(('dbt_project.yml', modified, io.file_hash(modified)))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-batch', type=Path, required=True)
    parser.add_argument('--leads', type=Path, required=True)
    parser.add_argument('--task', action='append', required=True)
    parser.add_argument('--mode', choices=('real-clock', 'fixed-clock'), default='real-clock')
    parser.add_argument('--clock-shim', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--concurrent', type=int, default=4)
    args = parser.parse_args()
    assert not args.output.exists() and str(args.output.resolve()).startswith('/scratch/')
    assert args.concurrent > 0 and len(args.task) == len(set(args.task))
    assert args.mode != 'fixed-clock' or args.clock_shim and args.clock_shim.is_file()
    base = io.read_json(args.base_batch / 'manifest.json')
    assert io.evidence_ok(base['config'], ROOT)
    config = io.read_json(ROOT / base['config']['path'])
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    args.output.mkdir(parents=True)
    shutil.copytree(args.base_batch / 'tools', args.output / 'tools', ignore=shutil.ignore_patterns('__pycache__'))
    config.update(job_name=args.output.name, jobs_dir=str(args.output/'jobs'), tasks=[],
                  n_attempts=2, n_concurrent_trials=args.concurrent)
    entries = {}
    with cached_hashes():
        for task_id in args.task:
            key = 'spider2__' + task_id
            assert key in base['tasks'] and task_id != 'movie_recomm001'
            source = args.base_batch / 'tasks' / key
            old = base['tasks'][key]
            assert io.tree_hash(source) == old['validation_copy_hash']
            assert old['backend_binding'] == binding_identity(ROOT, rows[key], 'apptainer')
            lead = args.leads / 'tasks' / task_id
            metadata = io.read_json(lead / 'result.json')
            assert metadata['task'] == task_id
            files = candidate_files(lead, metadata)
            if task_id == 'lever001':
                assert not files and metadata['group'] == 'A0_dbt_run_only'
            target = args.output / 'tasks' / key
            shutil.copytree(source, target, symlinks=True)
            # The original gold-copy oracle stays immutable in base-batch. This
            # isolated candidate carries no gold database or hidden test config.
            shutil.rmtree(target / 'solution')
            solution = target / 'solution'
            solution.mkdir()
            candidate = {'task_key': key, 'mode': args.mode, 'files': [],
                         'seed_first': task_id == 'apple_store001',
                         'reported_upstream': metadata['upstream'], 'reported_tag': metadata['tag'],
                         'reported_commit_prefix': metadata['commit_prefix']}
            for relative, path, sha in files:
                payload = Path('payload') / relative
                (solution / payload).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, solution / payload)
                candidate['files'].append({'destination': relative, 'payload': payload.as_posix(), 'sha256': sha})
            if args.mode == 'fixed-clock':
                shutil.copyfile(args.clock_shim, solution/'fakeclock.so')
                candidate['clock_shim'] = {'sha256': io.file_hash(args.clock_shim)}
            io.write_json(solution/'candidate.json', candidate)
            shutil.copyfile(ROOT/'scripts/spider_witness_build.py', solution/'build.py')
            (solution/'solve.sh').write_text('#!/bin/bash\nset -eu\npython3 /solution/build.py\n')
            spec = io.read_json(target/'mechanical/spec.json')
            spec.update(reference_kind='built_from_inputs', observation_sql_types=True)
            # Save every actual output column, preserving the reviewed native
            # projection in the source spec rather than silently dropping extras.
            for item in spec['outputs']:
                item['columns'] = None
                item['capture_scope'] = 'Every column of this explicitly reviewed output; native projection is recorded separately.'
            spec['files'] = list(dict.fromkeys([*spec.get('files', []), '/workspace/target/run_results.json',
                '/workspace/dbt_project.yml', *['/workspace/'+r for r, _, _ in files]]))
            io.write_json(target/'mechanical/spec.json', spec)
            assert io.file_hash(target/'tests/mechanical-native-test.sh') == old['native_verifier']['sha256']
            entry = deepcopy(old)
            for field, path in [('specification_source',target/'reviewed-source-spec.json'),
                ('normalized_specification',target/'mechanical/spec.json'),
                ('visibility_specification',target/'mechanical/visibility.json'),
                ('native_verifier',target/'tests/mechanical-native-test.sh'),('wrapper',target/'tests/test.sh')]:
                entry[field] = io.evidence_ref(path, ROOT)
            entry.update(validation_copy_hash=io.tree_hash(target), reference_kind='built_from_inputs',
                original_copy_gold_oracle=io.evidence_ref(source/'solution/solve.sh',ROOT),
                candidate_payload=io.evidence_ref(solution/'candidate.json',ROOT),
                candidate_provenance=io.evidence_ref(lead/'result.json',ROOT),
                build_script=io.evidence_ref(solution/'build.py',ROOT), mode=args.mode,
                scope='Candidate build execution only. Success, workflow witness, and semantics require review of actual trials.')
            entries[key] = entry
            config['tasks'].append({'path':str(target)})
            print('prepared',key,args.mode,flush=True)
    io.write_json(args.output/'config.yaml',check_harbor(config,[Path(t['path']) for t in config['tasks']]))
    io.write_json(args.output/'manifest.json', {'schema_version':1,'source':'spider2','tasks':entries,
        'planned_trials':2*len(entries),'config':io.evidence_ref(args.output/'config.yaml',ROOT),
        'base_batch':io.evidence_ref(args.base_batch/'manifest.json',ROOT),
        'lead_readme':io.evidence_ref(args.leads/'README.md',ROOT),
        'source_lock':base['source_lock'],'runtime_lock':base['runtime_lock'],
        'installation_bundle':base['installation_bundle'], 'model_requests':0,
        'tools':[io.evidence_ref(f,ROOT) for f in sorted((args.output/'tools').glob('*.py'))],
        'preparer':io.evidence_ref(Path(__file__),ROOT),
        'scope':'Two independent Harbor initializations per candidate using locked Qwen shell; no model calls, no gold access in solution payload, no verifier/image changes.'})
    print({'tasks':len(entries),'trials':2*len(entries),'mode':args.mode})


if __name__ == '__main__':
    main()
