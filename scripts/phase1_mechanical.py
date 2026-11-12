#!/usr/bin/env python3
"""Review mechanical evidence for every non-broken task, without model calls.

Writes immutable per-task review overlays, not the ledger. A gap is an observed
limit of the retained evidence, never a substitute for a successful execution.
"""
import argparse
from collections import Counter
from pathlib import Path
import tomllib
from review_tools import ROOT, cached_hashes
from dbt_bench import io
from dbt_bench.catalog import load_catalog
from dbt_bench.validation import identity, CHECKS
from dbt_bench.prepare import validate_layout

MECHANICAL = ('assets', 'reference', 'reference_repeat', 'nop', 'stability',
              'environment_verifier', 'environment_agent', 'cross_context', 'agent_visibility')


def refs_in(value):
    if isinstance(value, dict):
        if 'path' in value and 'sha256' in value:
            yield value
        else:
            for v in value.values():
                yield from refs_in(v)
    elif isinstance(value, list):
        for v in value:
            yield from refs_in(v)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--coverage', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--shared-qwen', type=Path, action='append', default=[])
    a = p.parse_args()
    if a.output.exists():
        p.error('Use a new output directory; previous evidence is immutable')
    a.output.mkdir(parents=True)
    coverage = io.read_json(a.coverage)
    assert coverage['coverage_complete'] and coverage['candidate_count'] == 135
    selected = {r['task_key']: r['selected'] for r in coverage['coverage']}
    records = {r['task_key']: r for r in io.read_jsonl(ROOT / 'validation/ledger.jsonl')}
    probes = a.shared_qwen + sorted((ROOT / 'artifacts/execution').glob('*qwen-production*/result.json'))
    summary = []
    isolated = []
    with cached_hashes():
        for row in load_catalog(ROOT):
            key = row['task_key']
            old = records[key]
            if old['native']['conclusion'] == 'broken':
                isolated.append(key)
                continue
            out = a.output / key
            out.mkdir()
            directory = ROOT / 'validation' / row['source'] / row['upstream_task_id']
            contract_path = directory / 'contract.yaml'
            contract = io.read_json(contract_path)
            current = identity(ROOT, row, contract_path, 'apptainer')
            binding_path = ROOT / 'catalog/backends/apptainer' / (key + '.json')
            binding = io.read_json(binding_path)
            task = ROOT / row['run_path']
            native_config = tomllib.loads((task / 'task.toml').read_text())
            validate_layout(task)
            asset_ok = (current['task'] == row['adapted_task_hash'] == binding['native_task_hash']
                        and current['backend_binding']['evidence_current']
                        and current['backend_binding']['image_sha256'] == binding['image']['sha256']
                        and current['backend_binding']['task_sha256'] == binding['run_hash'])
            observed = selected[key]
            assert observed['task_hash'] == row['adapted_task_hash']
            assert all(observed['binding'][k] == current['backend_binding'][k]
                       for k in ('binding_sha256', 'image_sha256', 'task_sha256'))
            trials = observed['trials']
            assert Counter(t['agent'] for t in trials) == {'oracle': 2, 'nop': 2}
            assert all(io.evidence_ok(t['result'], ROOT) for t in trials)
            oracle = sorted([t for t in trials if t['agent'] == 'oracle'], key=lambda t: t['finished_at'])
            nops = [t for t in trials if t['agent'] == 'nop']
            artifacts = []
            for trial in trials:
                base = (ROOT / trial['result']['path']).parent
                files = [f for f in base.rglob('*') if f.is_file()]
                artifacts.append({'trial_id': trial['trial_id'], 'files': [str(f.relative_to(base)) for f in files],
                                  'logs': [io.evidence_ref(f, ROOT) for f in files if f.suffix in {'.json', '.txt', '.log'}]})
            base_refs = [io.evidence_ref(task / f, ROOT) for f in ('instruction.md', 'task.toml', 'solution/solve.sh', 'tests/test.sh')]
            base_refs += [io.evidence_ref(binding_path, ROOT), io.evidence_ref(a.coverage, ROOT)]
            contexts, visibility = [], []
            for ref in binding['evidence']:
                if not ref['path'].endswith('.json'):
                    continue
                value = io.read_json(ROOT / ref['path'])
                if isinstance(value, dict) and 'integration_passed' in value and value.get('image', {}).get('sha256') == binding['image']['sha256']:
                    contexts.append({'evidence': ref, 'integration_passed': value['integration_passed'],
                                     'scope': value.get('scope'), 'checks': value.get('checks')})
                if isinstance(value, dict) and ('exact_hidden_matches' in value or 'agent_visibility_evidence' in value):
                    visibility.append({'evidence': ref, 'observation': value})
            matching_probes = []
            for path in probes:
                value = io.read_json(path)
                if (value.get('passed') and value.get('native_tool_context_probe')
                    and value.get('backend_binding', {}).get('image_sha256') == binding['image']['sha256']):
                    # Shared scope requires identical native setup configuration,
                    # not just an equal software base image.
                    producer_key = value['task_key']
                    producer = next(r for r in load_catalog(ROOT) if r['task_key'] == producer_key)
                    producer_cfg = tomllib.loads((ROOT / producer['run_path'] / 'task.toml').read_text())
                    if producer_cfg.get('environment') == native_config.get('environment'):
                        matching_probes.append({'evidence': io.evidence_ref(path, ROOT), 'producer_task': producer_key,
                                                'same_native_environment_configuration': True})
            audit = {
                'schema_version': 1, 'task_key': key, 'base_identity': current,
                'asset_identity_matches': asset_ok, 'native_reference_nop': observed,
                'native_reference_source': base_refs[2], 'native_verifier_entrypoint': base_refs[3],
                'retained_trial_artifact_inventory': artifacts, 'initial_context_evidence': contexts,
                'visibility_evidence': visibility, 'matching_actual_qwen_tool_probes': matching_probes,
                'native_environment_config': native_config.get('environment'),
                'native_verifier_config': native_config.get('verifier'),
                'witness_kind_before_semantic_review': contract['witness_kind'],
                'model_requests': 0, 'new_native_trials': 0,
                'scope': 'Execution and evidence coverage; accepting a native reference does not establish semantic correctness, output stability, or a genuine build witness.',
                'inputs': base_refs, 'reviewer': io.evidence_ref(Path(__file__), ROOT)}
            io.write_json(out / 'inspection.json', audit)
            inspection_ref = io.evidence_ref(out / 'inspection.json', ROOT)
            checks = {}
            def set_check(name, status, reason, extra=()):
                checks[name] = {'status': status, 'reason': reason, 'evidence': [inspection_ref, *extra]}
            set_check('assets', 'pass' if asset_ok else 'fail',
                      'Checked prepared native layout, source/task hash, bound task, image bytes and every provenance reference.')
            for name, trial in zip(('reference', 'reference_repeat'), oracle):
                success = trial['native_rewards'] == {'reward': 1.0} and trial['exception_info'] is None
                reason = ('Native reference accepted in independent clean initialization; full workflow witness and semantic validation are not established by these logs.'
                          if success else 'Native reference did not succeed; raw rewards and exception retained.')
                set_check(name, 'gap' if success else 'fail', reason, [trial['result'], base_refs[2]])
            nop_ok = all(t['native_rewards'] == {'reward': 0.0} and t['exception_info'] is None for t in nops)
            set_check('nop', 'pass' if nop_ok else 'gap',
                      'Two untouched clean initializations returned zero reward.' if nop_ok else 'Untouched baseline requires task-specific interpretation.',
                      [t['result'] for t in nops])
            set_check('stability', 'gap',
                      'Both native reference rewards are retained. Artifact inventory does not establish equality of all contract-required outputs; equal rewards alone are insufficient.')
            set_check('environment_verifier', 'gap',
                      'Reviewed native test entrypoint, two oracle and two nop logs, and matching image context evidence. Initial shell probes do not capture all versions/profiles/permissions inside this task verifier; actual per-task context scope remains incomplete.')
            set_check('environment_agent', 'pass' if matching_probes else 'gap',
                      'Pinned Qwen shell service executed pipe and PTY database probes on the same image and native environment configuration; each task is bound here. Scope is initial tool environment only.'
                      if matching_probes else 'Searched retained Qwen tool probes by exact image and native environment configuration; no matching complete probe. Initial shell context is retained but is insufficient to certify the actual Qwen tool context.',
                      [r['evidence'] for r in matching_probes])
            set_check('cross_context', 'gap',
                      'Native oracle output is read by the native verifier. No retained evidence here establishes an actual Qwen-tool-produced submission, declared transfer/rebuild, and verifier consumption for this task; source and output artifact inventory retained.')
            set_check('agent_visibility', 'gap',
                      'Reviewed image provenance and available initial hidden-file inventories. These prove only their declared paths/content-hash scan; task-specific full agent-visible gold/reference exclusion and runtime mount scope are not established by generic probes.')
            # Preserve prior decisions only when the full review identity and
            # evidence match. Never turn an existing weak-case gap into a pass.
            reused = []
            if old['identity'] == current and old.get('review_current'):
                for name in MECHANICAL:
                    prior = old['native']['checks'][name]
                    if (prior['status'] in {'pass', 'fail', 'gap'} and prior.get('evidence')
                        and all(io.evidence_ok(r, ROOT) for r in prior['evidence'])):
                        # An actual new tool probe may resolve a previously
                        # documented absence; other stronger evidence is kept.
                        if name == 'environment_agent' and prior['status'] == 'gap' and matching_probes:
                            continue
                        checks[name] = prior
                        reused.append(name)
            assert set(checks) == set(MECHANICAL)
            io.write_json(out / 'mechanical.json', {'schema_version': 1, 'task_key': key,
                'base_identity': current, 'checks': checks, 'reused_checks': reused,
                'inspection': inspection_ref, 'target': 'native'})
            summary.append({'task_key': key, 'review': io.evidence_ref(out / 'mechanical.json', ROOT),
                            'counts': dict(Counter(c['status'] for c in checks.values()))})
            print(key, summary[-1]['counts'], flush=True)
    assert len(summary) == 131 and len(isolated) == 4
    total = Counter()
    for item in summary:
        total.update(item['counts'])
    io.write_json(a.output / 'summary.json', {'schema_version': 1, 'phase': 1, 'scope': 'mechanical9 evidence review',
        'reviewed_tasks': len(summary), 'isolated_broken': isolated, 'check_counts': dict(total),
        'tasks': summary, 'ledger_updated': False, 'new_model_requests': 0, 'native_rewards_modified': False,
        'remaining': 'Semantic5 task-family review and root reconciliation/freezing. Gaps remain limitations, not certified passes.'})


if __name__ == '__main__':
    main()
