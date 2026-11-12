"""Recompute Workday case proofs and reconcile unchanged native rewards."""
import argparse
from collections import Counter, defaultdict
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dbt_bench.io import evidence_ref, file_hash, read_json, tree_hash, write_json
from reconstruction import INPUT_TABLES, exact_table_equal, verify_full_run
from independent import AGGREGATES, assess, encoded, recompute
from fixtures import construct
from workflow_proof import violations, EXPECTED_FAILURES


def reconcile(batch):
    batch = batch.resolve()
    manifest = read_json(batch / 'manifest.json')
    if manifest.get('backend_task_path') and tree_hash(ROOT / manifest['backend_task_path']) != manifest['backend_task_hash']:
        raise ValueError('Immutable shared backend task source changed')
    planned = {entry['task_label']: entry for entry in manifest['planned']}
    if len(planned) != len(manifest['planned']):
        raise ValueError('Duplicate planned task label')
    seen, ids, trials = set(), set(), []
    positives = defaultdict(list)
    for path in sorted((batch / 'jobs').glob('*/*/result.json')):
        raw = read_json(path)
        label = Path(raw['config']['task']['path']).name
        if label not in planned or label in seen or raw['id'] in ids:
            raise ValueError('Unknown or duplicate trial/task identity')
        seen.add(label)
        ids.add(raw['id'])
        entry = planned[label]
        if tree_hash(batch / 'tasks' / label) != entry['validation_copy_hash']:
            raise ValueError('Validation task copy changed')
        case_path = ROOT / entry['case']['path']
        if file_hash(case_path) != entry['case']['sha256'] or tree_hash(case_path.parent) != entry['case_hash']:
            raise ValueError('Case changed')
        case = read_json(case_path)
        for name, sha in case['helpers'].items():
            if file_hash(Path(__file__).parent / name) != sha:
                raise ValueError('Host proof code differs from executed code; use the frozen version')
        if file_hash(Path(__file__).parent.parent / 'reconstruction.py') != file_hash(case_path.parent / 'reconstruction.py'):
            raise ValueError('Reconstruction helper changed')
        agent = path.parent / 'agent'
        proof_path = agent / 'case-proof.json'
        valid = False
        differences = None
        workflow_failures = None
        if proof_path.exists():
            proof = read_json(proof_path)
            if proof['mode'] != case['mode'] or proof['data_scope'] != case['data_scope'] or proof['gold_access'] is not False:
                raise ValueError('Case proof identity/visibility differs')
            for field in ['input_artifacts', 'submission_artifacts']:
                if not proof[field]:
                    raise ValueError('Missing proof artifact')
                for ref in proof[field]:
                    if not ref['path'].startswith('/logs/agent/'):
                        raise ValueError('Undeclared proof artifact location')
                    local = agent / ref['path'].removeprefix('/logs/agent/')
                    if file_hash(local) != ref['sha256']:
                        raise ValueError('Proof artifact changed')
            pristine = read_json(agent / 'pristine-inputs.json')
            if hashlib.sha256((encoded(pristine) + '\n').encode()).hexdigest() != case['pristine_inputs_sha256']:
                raise ValueError('Wrong pristine input')
            inputs = read_json(agent / 'inputs.json')
            required_inputs = pristine
            if case['data_scope'] == 'additional_input_fixture':
                required_inputs, manual = construct(pristine)
                computed = {r['job_profile_id']: {key: r[key] for key in AGGREGATES} for r in recompute(inputs)}
                if computed != manual or manual != read_json(agent / 'manual-expectations.json'):
                    raise ValueError('Hand-calculated expectation differs')
            if set(inputs) != set(INPUT_TABLES) or not all(exact_table_equal(inputs[k], required_inputs[k]) for k in INPUT_TABLES):
                raise ValueError('Undeclared input variation')
            expected = recompute(inputs)
            if expected != read_json(agent / 'independent-expected.json'):
                raise ValueError('Independent recomputation differs')
            output = read_json(agent / 'output.json')
            differences = assess(expected, output)
            if differences or proof['semantic_differences']:
                raise ValueError('Workflow case output is not semantically correct')
            for ref in case['required_models_evidence']:
                if file_hash(ROOT / ref['path']) != ref['sha256']:
                    raise ValueError('Baseline model manifest changed')
                model_ids = sorted(k for k, n in read_json(ROOT / ref['path'])['nodes'].items()
                                   if n['resource_type'] == 'model' and n['config']['enabled'])
                if model_ids != case['required_model_ids']:
                    raise ValueError('Required model set differs from declared source')
            state = read_json(agent / 'workflow-state.json')
            if state['output_sha256'] != file_hash(agent / 'output.json') or state['commands'] != read_json(agent / 'commands.json'):
                raise ValueError('Workflow snapshot differs')
            for command in state['commands']:
                if file_hash(agent / command['log']) != command['log_sha256'] or command['returncode'] != 0:
                    raise ValueError('Command execution differs or failed')
            rr = read_json(agent / 'dbt-run_results.json') if (agent / 'dbt-run_results.json').exists() else None
            model_manifest = read_json(agent / 'dbt-manifest.json') if (agent / 'dbt-manifest.json').exists() else None
            workflow_failures = violations(state, rr, model_manifest, case['required_model_ids'])
            if workflow_failures != EXPECTED_FAILURES[case['mode']] or workflow_failures != proof['workflow_violations']:
                raise ValueError('Workflow proof differs')
            wrong = case['kind'] == 'negative'
            if bool(workflow_failures) != wrong:
                raise ValueError('Declared case is not established')
            validity = 'business_error' if wrong else 'legal_equivalent' if case['kind'] == 'equivalent' else 'built_from_inputs'
            if proof['validity'] != validity:
                raise ValueError('Wrong proof validity')
            if rr is not None:
                if len(rr['results']) != proof['executed_models']:
                    raise ValueError('Executed model count differs')
                if read_json(agent / 'dependency-inventory.json') != read_json(case_path.parent / 'dependency-inventory.json'):
                    raise ValueError('Dependency identity differs')
            valid = True
            if case['mode'].endswith('-reference'):
                positives[case['mode']].append({'expected': expected, 'raw_output_sha256': file_hash(agent / 'output.json')})
        reward = (raw.get('verifier_result') or {}).get('rewards')
        exception = raw.get('exception_info')
        log_path = path.parent / 'verifier/test-stdout.txt'
        log = log_path.read_text() if log_path.exists() else ''
        outcome = 'unresolved_execution'
        if valid and exception is None:
            if reward == {'reward': 1.0} and 'SUCCESS: All tables match gold standard' in log:
                outcome = 'missed_wrong' if case['kind'] == 'negative' else 'accepted_correct'
            elif reward == {'reward': 0.0} and "FAIL: Table 'workday__job_overview' does not match gold standard" in log and 'ERROR:' not in log and 'Traceback' not in log:
                outcome = 'detected_wrong' if case['kind'] == 'negative' else 'rejected_legal'
        trials.append({**entry, 'trial_id': raw['id'], 'native_rewards': reward, 'exception_info': exception,
                       'outcome': outcome, 'proof_recomputed': valid, 'independent_differences': differences, 'workflow_violations': workflow_failures,
                       'result': evidence_ref(path, ROOT),
                       'independent_proof': evidence_ref(proof_path, ROOT) if proof_path.exists() else None,
                       'native_log': evidence_ref(log_path, ROOT) if log_path.exists() else None})
    report = {
        'schema_version': 1, 'planned': len(planned), 'observed': len(trials),
        'complete': seen == set(planned), 'missing': sorted(set(planned) - seen),
        'outcomes': dict(Counter(t['outcome'] for t in trials)),
        'data_scopes': dict(Counter(t['data_scope'] for t in trials)),
        'positive_stability': {mode: {'runs': len(rows),
            'semantic_equal': len(rows) >= 2 and all(r['expected'] == rows[0]['expected'] for r in rows),
            'raw_output_equal': len({r['raw_output_sha256'] for r in rows}) == 1} for mode, rows in positives.items()},
        'native_conclusion': 'weak', 'audit_conclusion': 'pending', 'protocol_checks_promoted': 0,
        'trials': trials, 'full_validation_complete': False,
        'scope': 'Independent case proof with unchanged native checker. Original-input and additional-input fixture trials remain separate. This helper is not a certified audit checker.',
    }
    destination = batch / 'reconciliation.json'
    if destination.exists():
        raise ValueError('Reconciliation already exists; preserve original evidence')
    write_json(destination, report)
    print({key: value for key, value in report.items() if key not in ['trials', 'scope']})
    return 0 if report['complete'] and all(t['outcome'] != 'unresolved_execution' for t in trials) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    raise SystemExit(reconcile(parser.parse_args().batch))
