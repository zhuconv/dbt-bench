"""Apply the independent audit to 29 immutable semantic/workflow captures."""
import argparse
from collections import Counter
from pathlib import Path
import sys
from checker import score
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from capture import verify
from dbt_bench.io import evidence_ref, read_json, write_json, evidence_ok

BATCHES = ['session-semantics-pilot-v2', 'session-semantics-cases-v2', 'session-workflow-cases-v1']


def run(out):
    out.mkdir(parents=True, exist_ok=False)
    ref = lambda p: evidence_ref(p, ROOT)
    rows, seen, sources = [], set(), []
    for name in BATCHES:
        batch = ROOT / 'artifacts/validation' / name
        reconciliation = read_json(batch / 'reconciliation.json')
        assert reconciliation['complete'] and not reconciliation['missing']
        sources.append(ref(batch / 'reconciliation.json'))
        for trial in reconciliation['trials']:
            assert trial['trial_id'] not in seen and trial['proof_recomputed']
            seen.add(trial['trial_id'])
            agent, proof = verify(ROOT, batch, trial)
            is_workflow = 'phases' in proof
            final_phase = proof['phases'][-1] if is_workflow else 'after-case'
            request = dict(schema_version=1, task_key='deb__dbt-session-attribution',
                data_scope=trial['data_scope'], inputs=ref(agent / 'evaluated-inputs.json'),
                output=ref(agent / final_phase / 'snapshot.json'),
                output_kind='workflow_snapshot' if is_workflow else 'semantic_snapshot', workflow=None)
            if is_workflow:
                request['workflow'] = {'kind': 'reviewed_validation_case_snapshots',
                    'before': ref(agent / 'before-case/snapshot.json'),
                    'phases': [ref(agent / p / 'snapshot.json') for p in proof['phases']]}
            path = out / 'inputs' / (trial['trial_id'] + '.json')
            write_json(path, request)
            result = score(ROOT, path)
            # Independent original case validity is compared only after scoring;
            # it is not an input to the checker or its recomputation.
            if trial['kind'] == 'negative':
                assert result['status'] == 'fail', (trial['mode'], result)
            else:
                assert result['data']['status'] == 'pass'
                assert result['status'] == ('pass' if is_workflow else 'unknown')
            result_path = out / 'audits' / (trial['trial_id'] + '.json')
            write_json(result_path, result)
            rows.append(dict(trial_id=trial['trial_id'], mode=trial['mode'], data_scope=trial['data_scope'],
                native_result=trial['result'], native_rewards=trial['native_rewards'], independent_case_kind=trial['kind'],
                independent_proof=trial['independent_proof'], input_manifest=ref(path), audit=ref(result_path),
                status=result['status'], audit_reward=result['audit_reward'],
                data_status=result['data']['status'], workflow_status=result['workflow']['status']))
    summary = dict(schema_version=1, task_key='deb__dbt-session-attribution', certified=False,
        records=rows, evaluated=len(rows), statuses=dict(Counter(r['status'] for r in rows)),
        data_statuses=dict(Counter(r['data_status'] for r in rows)),
        workflow_statuses=dict(Counter(r['workflow_status'] for r in rows)),
        data_scopes=dict(Counter(r['data_scope'] for r in rows)), source_reconciliations=sources,
        checker=ref(HERE / 'checker.py'), input_policy=ref(HERE / 'input-policy.json'),
        capture_binding=ref(HERE / 'capture.py'), runner=ref(Path(__file__)),
        new_benchmark_trials=0, new_model_trials=0, new_submission_replays=0,
        scope='Independent post-hoc audits on exact retained captures. Native scores remain immutable. Original-input and additional-input fixtures stay separate; production capture and full protocol are not certified.')
    assert summary['evaluated'] == 29 and summary['statuses'] == {'pass': 2, 'fail': 22, 'unknown': 5}
    assert summary['data_statuses'] == {'pass': 11, 'fail': 18}
    write_json(out / 'summary.json', summary)
    print({k:summary[k] for k in ['evaluated','statuses','data_statuses','workflow_statuses','data_scopes']})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    run(parser.parse_args().output_dir.resolve())
