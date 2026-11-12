#!/usr/bin/env python3
"""Prepare native cases on fresh blind builds, using one shared no-model adapter."""
import argparse
from copy import deepcopy
from pathlib import Path
import re
import shutil
from dbt_bench import io
from dbt_bench.prepare import check_harbor
from prepare_mechanical_cases import observation_spec
from review_tools import ROOT


def proof_gate_source(kind):
    if kind not in {'negative', 'equivalent'}:
        raise ValueError('Unknown proof kind')
    expected = 'business_error' if kind == 'negative' else 'legal_equivalent'
    return ('import json\nfrom pathlib import Path\n'
        "p=Path('/logs/agent/case-proof.json')\n"
        "d=json.loads(p.read_text()) if p.exists() else {}\n"
        f"assert d.get('validity') == {expected!r}, 'Invalid case construction; native verifier not scored'\n"
        "assert d.get('tested_checker_invoked_to_define_validity') is False\n")


def prepare(base_batch, requests_path, output, concurrent=4):
    base_batch, requests_path, output = map(Path, (base_batch, requests_path, output))
    if output.exists() or not str(output.resolve()).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH case batch')
    base = io.read_json(base_batch / 'manifest.json')
    req = io.read_json(requests_path)
    if base.get('phase') != 4 or base['model_requests'] != 0 or req['model_requests'] != 0:
        raise ValueError('Require a phase 4 no-model blind build and no-model cases')
    if not io.evidence_ok(base['config'], ROOT):
        raise ValueError('Base config changed')
    cfg = io.read_json(ROOT / base['config']['path'])
    if cfg['agents'][0]['import_path'] != 'audit_rebuild_agent:AuditRebuildAgent':
        raise ValueError('Cases must start from actual author reconstruction adapter')
    labels = [r['label'] for r in req['tasks']]
    if len(labels) != len(set(labels)) or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', l) for l in labels):
        raise ValueError('Unsafe/duplicate case labels')
    for row in req['tasks']:
        source = base['tasks'][row['base_label']]
        if row['task_key'] != source['task_key'] or row['kind'] not in {'negative', 'equivalent'}:
            raise ValueError('Case task/kind mismatch')
        override = row.get('request_override', {})
        if (set(override) - {'probe_backend_before_submission'}
                or any(type(v) is not bool for v in override.values())
                or override and row['kind'] != 'negative'):
            raise ValueError('Only explicit backend-probe omission is a supported negative setup override')
        if io.tree_hash(base_batch / 'tasks' / row['base_label']) != source['validation_copy_hash']:
            raise ValueError('Formal reconstruction task changed')
        for ref in [row['engine']] + [f['source'] for f in row.get('support_files', [])]:
            if not io.evidence_ok(ref, ROOT):
                raise ValueError('Case code changed')
    output.mkdir(parents=True)
    shutil.copyfile(__file__, output / 'preparation-source.py')
    shutil.copytree(base_batch / 'tools', output / 'tools')
    shutil.copyfile(ROOT / 'scripts/phase4_case_agent.py', output / 'tools/phase4_case_agent.py')
    cfg['agents'][0]['import_path'] = 'phase4_case_agent:Phase4CaseAgent'
    cfg.update(job_name=output.name, jobs_dir=str(output / 'jobs'), n_attempts=1,
               n_concurrent_trials=concurrent, tasks=[])
    entries = {}
    for row in req['tasks']:
        entry = deepcopy(base['tasks'][row['base_label']])
        task = output / 'tasks' / row['label']
        shutil.copytree(base_batch / 'tasks' / row['base_label'], task)
        override = row.get('request_override', {})
        if override:
            rebuilt = io.read_json(task / 'audit-rebuild/request.json')
            rebuilt.update(override)
            io.write_json(task / 'audit-rebuild/request.json', rebuilt)
        payload = task / 'phase4-case'; payload.mkdir()
        shutil.copyfile(ROOT / row['engine']['path'], payload / 'case_engine.py')
        reserved = {'case_engine.py', 'case.json', 'proof_gate.py', 'build_gate.py'}
        for file in row.get('support_files', []):
            name = Path(file['path'])
            if name.is_absolute() or '..' in name.parts or str(name) in reserved:
                raise ValueError('Unsafe case support path')
            dest = payload / name; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / file['source']['path'], dest)
        io.write_json(payload / 'case.json', row['spec'])
        (payload / 'build_gate.py').write_text(
            'import json\nfrom pathlib import Path\n'
            "p=Path('/logs/agent/audit-rebuild/execution.json')\n"
            "d=json.loads(p.read_text()) if p.exists() else {}\n"
            "assert d.get('build_success') is True, 'Blind build failed; do not construct a semantic case'\n")
        expected = 'business_error' if row['kind'] == 'negative' else 'legal_equivalent'
        (payload / 'proof_gate.py').write_text(proof_gate_source(row['kind']))
        spec_path = task / 'mechanical/spec.json'
        spec = io.read_json(spec_path)
        spec.update(row.get('observation', {}))
        spec = observation_spec(spec, row['spec'])
        io.write_json(spec_path, spec)
        wrapper = task / 'tests/test.sh'
        wrapper.rename(task / 'tests/phase4-observed-test.sh')
        wrapper.write_text('#!/bin/bash\nset -e\npython3 /tmp/dbt-bench-phase4-case/proof_gate.py\nexec bash /tests/phase4-observed-test.sh\n')
        native = task / 'tests/mechanical-native-test.sh'
        if io.file_hash(native) != entry['native_verifier']['sha256']:
            raise ValueError('Native verifier changed')
        entry.update(validation_copy_hash=io.tree_hash(task), native_verifier=io.evidence_ref(native, ROOT),
            request=io.evidence_ref(task / 'audit-rebuild/request.json', ROOT),
            case_setup={'kind': row['kind'], 'case': io.evidence_ref(payload / 'case.json', ROOT),
                'engine': io.evidence_ref(payload / 'case_engine.py', ROOT),
                'expected_validity': expected, 'base_label': row['base_label'],
                'request_override': override, 'original_rebuild_request': base['tasks'][row['base_label']]['request'],
                'scope': row.get('scope', 'Actual fresh author build followed by independently proved submission case')})
        entries[row['label']] = entry
        cfg['tasks'].append({'path': str(task)})
    io.write_json(output / 'config.yaml', check_harbor(cfg, [Path(t['path']) for t in cfg['tasks']]))
    io.write_json(output / 'manifest.json', {'schema_version': 1, 'phase': 4,
        'kind': 'audit_clean_reconstruction', 'model_requests': 0,
        'base_batch': io.evidence_ref(base_batch / 'manifest.json', ROOT),
        'requests': io.evidence_ref(requests_path, ROOT), 'tasks': entries,
        'planned_trials': len(entries), 'config': io.evidence_ref(output / 'config.yaml', ROOT),
        'tools': [io.evidence_ref(p, ROOT) for p in (output / 'tools').glob('*.py')],
        'preparation_code': io.evidence_ref(output / 'preparation-source.py', ROOT),
        'scope': 'Proof-gated native semantic/equivalent cases after actual locked-Qwen author builds. No model requests.'})
    return {'tasks': len(entries), 'trials': len(entries), 'config': str(output / 'config.yaml')}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-batch', required=True, type=Path)
    p.add_argument('--requests', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--concurrent', type=int, default=4)
    a = p.parse_args()
    print(prepare(a.base_batch, a.requests, a.output, a.concurrent))
