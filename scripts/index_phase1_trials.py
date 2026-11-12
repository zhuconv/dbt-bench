#!/usr/bin/env python3
"""Append phase-1 terminal results and retain unfinished/startup-failed plans."""
from collections import Counter
from pathlib import Path
import shutil
from review_tools import ROOT
from dbt_bench import io
from prepare_batch_completion import validate_completion_config, no_model_agent_key


def planned_slots(config):
    agents = [no_model_agent_key(a) for a in config['agents']]
    paths = [str(Path(t['path']).resolve()) for t in config['tasks']]
    assert len(agents) == len(set(agents)) and len(paths) == len(set(paths))
    assert config['n_attempts'] > 0
    return Counter({(path, agent): config['n_attempts'] for path in paths for agent in agents})


def trial_kind(config, proof, case_spec=None):
    """Keep changed-input checks separate, including their failed executions."""
    if config.get('verifier', {}).get('disable'):
        return 'environment_probe'
    scopes = {proof.get('case_scope'), proof.get('data_scope')}
    if scopes & {'controlled_input_fixture', 'additional_input_fixture', 'fixture_input'}:
        return 'additional_fixture'
    if (case_spec or {}).get('controlled_fixture'):
        # A shared case spec may carry the fixture even for original-input
        # controls. Execution must say which input was actually selected.
        if not scopes & {'original_input', 'original_pinned_inputs'}:
            return 'input_scope_unknown'
    return 'native'


def case_dispositions(batch, manifest, root):
    """Retain reviewed validity corrections separately from raw proof/reward."""
    path = batch / 'case-validity-dispositions-v1.json'
    if not path.exists():
        return None, {}
    value = io.read_json(path)
    assert value['manifest'] == io.evidence_ref(batch / 'manifest.json', root)
    assert value['raw_rewards_modified'] is False and value['model_requests'] == 0
    found = {}
    for item in value['excluded_cases']:
        label = item['case_label']
        assert label in manifest['tasks'] and label not in found
        assert manifest['tasks'][label]['task_key'] == item['task_key']
        assert item['coverage_eligible'] is False
        assert item['independent_validity'] in {'not_established', 'invalid'}
        assert io.evidence_ok(item['adjudication'], root)
        found[label] = item
    return io.evidence_ref(path, root), found


def main():
    review = ROOT / 'artifacts/validation/phase1-review-v1'
    current = review / 'retained-trial-index.json'
    previous = review / 'retained-trial-index-before-phase1-batches.json'
    if not previous.exists():
        shutil.copyfile(current, previous)
    index = io.read_json(previous)
    trials = {t['trial_id']: t for t in index['trials']}
    plans, added = [], []
    completions = {}
    for path in sorted((ROOT / 'artifacts/validation').glob('*/completion.json')):
        value = io.read_json(path)
        if 'original_manifest' in value:
            key = (ROOT / value['original_manifest']['path']).resolve()
            completions.setdefault(key, []).append((path, value))
    prefixes = ('native-mechanical-', 'native-monthly-', 'native-deb-', 'native-airbnb', 'native-risk-',
                'native-f1003-', 'native-f1011-', 'native-rfm-', 'native-ade-', 'native-initial-', 'spider-v3-', 'spider-v4-', 'native-spider-',
                'native-geographic-', 'native-fulfillment-', 'native-reconciliation-', 'native-weekly-', 'native-daily-')
    for batch in sorted((ROOT / 'artifacts/validation').iterdir()):
        if not batch.name.startswith(prefixes) or not (batch / 'manifest.json').is_file():
            continue
        manifest = io.read_json(batch / 'manifest.json')
        if not isinstance(manifest.get('config'), dict):
            continue
        assert io.evidence_ok(manifest['config'], ROOT)
        config = io.read_json(ROOT / manifest['config']['path'])
        disposition_ref, dispositions = case_dispositions(batch, manifest, ROOT)
        expected = planned_slots(config)
        job = Path(config['jobs_dir']) / config['job_name']
        jobs = [job]
        recovery_refs = []
        for path, recovery in completions.get((batch / 'manifest.json').resolve(), []):
            assert recovery['original_manifest'] == io.evidence_ref(batch / 'manifest.json', ROOT)
            assert recovery['original_config'] == io.evidence_ref(batch / 'config.yaml', ROOT)
            assert io.evidence_ok(recovery['stopped_evidence'], ROOT)
            assert all(io.evidence_ok(r, ROOT) for r in recovery['retained_results'])
            recovery_refs.append(io.evidence_ref(path, ROOT))
            for plan in recovery['plans']:
                assert io.evidence_ok(plan['config'], ROOT)
                resumed = io.read_json(ROOT / plan['config']['path'])
                validate_completion_config(config, resumed)
                jobs.append(Path(resumed['jobs_dir']) / resumed['job_name'])
        planned = sum(expected.values())
        observed = []
        task_counts = Counter()
        known_paths = {str(Path(t['path']).resolve()) for t in config['tasks']}
        unfinished = []
        for path in sorted(p for directory in jobs for p in directory.glob('*/result.json')):
            raw = io.read_json(path)
            assert str(Path(raw['config']['task']['path']).resolve()) in known_paths
            if not raw.get('finished_at'):
                unfinished.append(str(path))
                continue
            slot = (str(Path(raw['config']['task']['path']).resolve()), no_model_agent_key(raw['config']['agent']))
            task_counts[slot] += 1
            assert task_counts[slot] <= expected[slot], 'Unexpected agent or excess attempts for one planned task/agent'
            proof_path = path.parent / 'agent/case-proof.json'
            proof = io.read_json(proof_path) if proof_path.exists() else {}
            task_entries = manifest.get('tasks', {})
            task_entry = task_entries.get(Path(raw['config']['task']['path']).name, {}) if isinstance(task_entries, dict) else {}
            case_ref = task_entry.get('case')
            if case_ref:
                assert io.evidence_ok(case_ref, ROOT)
            case_spec = io.read_json(ROOT / case_ref['path']) if case_ref else {}
            kind = trial_kind(config, proof, case_spec)
            entry = {'trial_id': raw['id'], 'kind': kind, 'result': io.evidence_ref(path, ROOT)}
            disposition = dispositions.get(Path(raw['config']['task']['path']).name)
            if disposition:
                entry['case_validity_adjudication'] = {**disposition, 'batch_dispositions': disposition_ref}
            if proof_path.exists():
                entry['input_scope_evidence'] = io.evidence_ref(proof_path, ROOT)
            assert raw['id'] not in observed, 'Duplicate terminal ID within batch'
            observed.append(raw['id'])
            if raw['id'] in trials:
                assert trials[raw['id']]['result']['sha256'] == entry['result']['sha256']
                if disposition:
                    trials[raw['id']]['case_validity_adjudication'] = entry['case_validity_adjudication']
            else:
                trials[raw['id']] = entry
                added.append(entry)
        assert len(observed) <= planned
        directories_without_result = [str(p) for directory in jobs if directory.exists()
                                      for p in directory.iterdir() if p.is_dir() and not (p / 'result.json').exists()]
        plans.append({'batch': batch.name, 'planned': planned, 'terminal': len(observed),
            'remaining_slots': planned - len(observed), 'nonterminal_results': unfinished,
            'directories_without_terminal_result': directories_without_result,
            'manifest': io.evidence_ref(batch / 'manifest.json', ROOT), 'model_requests': 0,
            'case_validity_dispositions': disposition_ref,
            'completion_manifests': recovery_refs,
            'superseded': io.evidence_ref(batch / 'superseded.json', ROOT) if (batch / 'superseded.json').exists() else None})
    index.update(trials=list(trials.values()), counts=dict(Counter(t['kind'] for t in trials.values())),
        phase1_new_trials=added, phase1_plans=plans, phase1_added_terminal_count=len(added),
        phase1_previous=io.evidence_ref(previous, ROOT), new_model_requests=0,
        scope='All prior records plus terminal phase-1 results. Environment-only probes have no native reward; failed constructions and unfinished/startup-aborted plans remain explicit.')
    io.write_json(current, index)
    print({'counts': index['counts'], 'new_terminal': len(added), 'plans': len(plans)})


if __name__ == '__main__':
    main()
