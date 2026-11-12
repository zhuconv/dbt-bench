#!/usr/bin/env python3
"""Reuse prepared source batches for isolated, proof-gated native verifier cases."""
import argparse
import re
from pathlib import Path
import shutil
from review_tools import ROOT
from dbt_bench import io
from dbt_bench.prepare import check_harbor


def observation_spec(base, case):
    """Add explicitly reviewed files without broad directory/glob capture."""
    result = dict(base)
    paths = case.get('observation_files', [])
    assert isinstance(paths, list) and len(paths) <= 64
    for value in paths:
        assert isinstance(value, str) and Path(value).is_absolute()
        assert len(Path(value).parts) > 2 and '..' not in Path(value).parts
        assert not any(c in value for c in '*?[]'), 'Declare individual files, not globs'
    result['files'] = list(dict.fromkeys([*base.get('files', []), *paths]))
    if case.get('observation_sql_types'):
        result['observation_sql_types'] = True
    return result


def variants(entry, kinds=None):
    """Explicit unique case labels allow multiple rule cases for one task."""
    values = entry.get('variants')
    if values is None:
        values = [{'kind': k, 'label': k, 'spec': entry[k]}
                  for k in ('equivalent', 'negative', 'reference_relative') if entry.get(k) is not None]
    selected = []
    seen = set()
    for value in values:
        kind, label, spec = value['kind'], value['label'], value['spec']
        assert kind in ('equivalent', 'negative', 'reference_relative') and isinstance(spec, dict)
        if value.get('expected_validity'):
            assert value['expected_validity'] == {'negative': 'business_error', 'equivalent': 'legal_equivalent',
                'reference_relative': 'reference_relative_equivalent'}[kind], 'Case kind contradicts declared validity'
        assert re.fullmatch(r'[a-z0-9][a-z0-9_-]*', label) and label not in seen
        seen.add(label)
        if not kinds or kind in kinds:
            selected.append((kind, label, spec))
    return selected


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, required=True)
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--engine', type=Path, required=True)
    p.add_argument('--engine-sha256', required=True)
    p.add_argument('--task', action='append')
    p.add_argument('--kind', choices=('equivalent', 'negative', 'reference_relative'), action='append')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--concurrent', type=int, default=4)
    a = p.parse_args()
    assert io.file_hash(a.engine) == a.engine_sha256
    assert not a.output.exists() and str(a.output.resolve()).startswith('/scratch/')
    base_manifest = io.read_json(a.batch / 'manifest.json')
    base_config = io.read_json(ROOT / base_manifest['config']['path'])
    entries = io.read_json(a.cases)['tasks']
    if a.task:
        entries = [e for e in entries if e['task_key'] in a.task]
        assert {e['task_key'] for e in entries} == set(a.task)
    entries = [e for e in entries if e['task_key'] in base_manifest['tasks']]
    assert entries
    a.output.mkdir(parents=True)
    shutil.copytree(a.batch / 'tools', a.output / 'tools', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(ROOT / 'scripts/native_mechanical_agent.py', a.output / 'tools/native_mechanical_agent.py')
    shutil.copyfile(ROOT / 'scripts/mechanical_observer.py', a.output / 'tools/mechanical_observer.py')
    config = {**base_config, 'job_name': a.output.name, 'jobs_dir': str(a.output / 'jobs'),
              'n_attempts': 1, 'n_concurrent_trials': a.concurrent, 'tasks': []}
    tasks = {}
    for task_cases in entries:
        key = task_cases['task_key']
        source = a.batch / 'tasks' / key
        assert io.tree_hash(source) == base_manifest['tasks'][key]['validation_copy_hash']
        for kind, case_label, spec in variants(task_cases, a.kind):
            label = key + '--' + case_label
            assert label not in tasks, 'Duplicate case: ' + label
            target = a.output / 'tasks' / label
            shutil.copytree(source, target)
            observer_spec_path = target / 'mechanical/spec.json'
            io.write_json(observer_spec_path, observation_spec(io.read_json(observer_spec_path), spec))
            shutil.copyfile(a.output / 'tools/mechanical_observer.py', target / 'mechanical/mechanical_observer.py')
            reference_setup = spec.get('reference_setup', 'native_reference')
            assert reference_setup in ('native_reference', 'skip_native_reference')
            skipped_reference = None
            if reference_setup == 'skip_native_reference':
                assert spec.get('reference_setup_reason'), 'Skipping native reference requires explicit workflow-case rationale'
                solve = target / 'solution/solve.sh'
                skipped_reference = io.evidence_ref(source / 'solution/solve.sh', ROOT)
                solve.write_text('#!/bin/bash\n# Explicit validation case: construction happens in the proof hook.\ntrue\n')
            payload = target / 'mechanical-case'
            payload.mkdir()
            shutil.copyfile(a.engine, payload / 'case_engine.py')
            io.write_json(payload / 'case.json', spec)
            test = target / 'tests/test.sh'
            previous_wrapper = test.read_bytes()
            test.rename(target / 'tests/mechanical-observed-test.sh')
            expected = {'negative': 'business_error', 'equivalent': 'legal_equivalent',
                        'reference_relative': 'reference_relative_equivalent'}[kind]
            gate = ('import json,sys\nfrom pathlib import Path\n'
                    "p=Path('/logs/agent/case-proof.json')\n"
                    'd=json.loads(p.read_text()) if p.exists() else {}\n'
                    f'assert d.get("validity")=={expected!r}, "Case construction failed; not scored"\n'
                    'assert d.get("tested_checker_invoked_to_define_validity") is False\n')
            if kind == 'reference_relative':
                gate += 'assert d.get("baseline_correctness") == "unresolved"\n'
            (payload / 'proof_gate.py').write_text(gate)
            test.write_text('#!/bin/bash\nset -e\npython3 /tmp/dbt-bench-case/proof_gate.py\nexec bash /tests/mechanical-observed-test.sh\n')
            assert (target / 'tests/mechanical-observed-test.sh').read_bytes() == previous_wrapper
            assert io.file_hash(target / 'tests/mechanical-native-test.sh') == base_manifest['tasks'][key]['native_verifier']['sha256']
            config['tasks'].append({'path': str(target)})
            tasks[label] = {'task_key': key, 'kind': kind, 'case': io.evidence_ref(payload / 'case.json', ROOT),
                'engine': io.evidence_ref(payload / 'case_engine.py', ROOT),
                'validation_copy_hash': io.tree_hash(target),
                'native_verifier': io.evidence_ref(target / 'tests/mechanical-native-test.sh', ROOT),
                'base_task': base_manifest['tasks'][key],
                'reference_setup': reference_setup, 'skipped_native_reference': skipped_reference,
                'observation_specification': io.evidence_ref(observer_spec_path, ROOT),
                'scope': task_cases.get('scope')}
    io.write_json(a.output / 'config.yaml', check_harbor(config, [Path(t['path']) for t in config['tasks']]))
    io.write_json(a.output / 'manifest.json', {'schema_version': 1, 'tasks': tasks,
        'planned_trials': len(tasks), 'model_requests': 0, 'config': io.evidence_ref(a.output / 'config.yaml', ROOT),
        'base_batch': io.evidence_ref(a.batch / 'manifest.json', ROOT),
        'case_specifications': io.evidence_ref(a.cases, ROOT), 'tools': [io.evidence_ref(f, ROOT) for f in (a.output / 'tools').glob('*.py')],
        'scope': 'Actual full unchanged native verifier on isolated proof-gated submissions. Invalid construction has no reward; original task/native rewards are untouched.'})
    print({'tasks': len(entries), 'planned_case_trials': len(tasks), 'config': str(a.output / 'config.yaml')})


if __name__ == '__main__':
    main()
