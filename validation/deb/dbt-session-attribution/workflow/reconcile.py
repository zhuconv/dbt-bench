"""Recompute workflow validity from saved artifacts before reading native outcomes."""
import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
BASE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'src'), str(BASE.parent / 'semantics')]
from independent import calculate
from common import assess
from dbt_bench.io import evidence_ref, file_hash, read_json, tree_hash, write_json


def reconcile(batch):
    manifest = read_json(batch / 'manifest.json')
    planned = {r['task_label']: r for r in manifest['planned']}
    assert len(planned) == len(manifest['planned'])
    seen, ids, trials = set(), set(), []
    for path in sorted((batch / 'jobs').glob('*/*/result.json')):
        raw = read_json(path)
        label = Path(raw['config']['task']['path']).name
        assert label in planned and label not in seen and raw['id'] not in ids
        seen.add(label)
        ids.add(raw['id'])
        entry = manifest['tasks'][label]
        assert tree_hash(batch / 'tasks' / label) == entry['validation_copy_hash']
        case_path = ROOT / entry['case']['path']
        assert file_hash(case_path) == entry['case']['sha256'] and tree_hash(case_path.parent) == entry['case_hash']
        case = read_json(case_path)
        assert file_hash(case_path.parent / 'common.py') == file_hash(BASE / 'common.py')
        assert file_hash(case_path.parent / 'independent.py') == file_hash(BASE.parent / 'semantics/independent.py')
        agent = path.parent / 'agent'
        proof_path = agent / 'case-proof.json'
        proof = read_json(proof_path) if proof_path.exists() else None
        verified, facts = False, None
        if proof:
            assert proof['case'] == case['mode'] and proof['data_scope'] == case['data_scope']
            assert not proof['tested_checker_invoked_to_define_validity']
            assert proof['requirement_ids'] == case['requirement_ids']
            for field in ['input_artifacts', 'submission_artifacts']:
                for ref in proof[field]:
                    assert ref['path'].startswith('/logs/agent/')
                    assert file_hash(agent / ref['path'].removeprefix('/logs/agent/')) == ref['sha256']
            assert file_hash(agent / 'evaluated-inputs.json') == case['pristine_inputs_sha256']
            expectation = calculate(read_json(agent / 'evaluated-inputs.json'))
            assert not expectation['unresolved'] and expectation == read_json(agent / 'independent-expectation.json')
            snapshots = {}
            for phase in ['before-case'] + proof['phases']:
                snap = read_json(agent / phase / 'snapshot.json')
                for ref in snap['files']:
                    assert ref['path'].startswith('/logs/agent/' + phase + '/')
                    assert file_hash(agent / ref['path'].removeprefix('/logs/agent/')) == ref['sha256']
                model = agent / phase / 'models/marts/attribution_report.sql'
                assert model.exists() == snap['model_present']
                assert (file_hash(model) if model.exists() else None) == snap['model_sha256']
                snapshots[phase] = snap
            facts = assess(case['mode'], snapshots['before-case'], [snapshots[p] for p in proof['phases']], expectation['expected'])
            assert facts == proof['facts']
            verified = True
        # Only after the independent evidence has been checked, read native scoring.
        rewards = (raw.get('verifier_result') or {}).get('rewards')
        exception = raw.get('exception_info')
        log_path = path.parent / 'verifier/test-stdout.txt'
        log = log_path.read_text() if log_path.exists() else ''
        outcome = 'unresolved_execution'
        if verified and exception is None:
            if rewards == {'reward': 1.0}:
                outcome = 'missed_wrong' if case['kind'] == 'negative' else 'accepted_correct'
            elif rewards == {'reward': 0.0} and 'AssertionError:' in log and 'ERROR at setup' not in log:
                outcome = 'detected_wrong' if case['kind'] == 'negative' else 'rejected_legal'
        trials.append({**planned[label], 'trial_id': raw['id'], 'kind': case['kind'],
            'requirements': case['requirement_ids'], 'proof_recomputed': verified, 'independent_facts': facts,
            'independent_proof': evidence_ref(proof_path, ROOT) if proof else None,
            'outcome': outcome, 'native_rewards': rewards, 'exception_info': exception,
            'result': evidence_ref(path, ROOT), 'native_log': evidence_ref(log_path, ROOT) if log_path.exists() else None})
    result = {'schema_version': 1, 'planned': len(planned), 'observed': len(trials),
        'complete': set(planned) == seen, 'missing': sorted(set(planned) - seen),
        'outcomes': dict(Counter(t['outcome'] for t in trials)), 'trials': trials,
        'qwen_smoke': False, 'data_scope': 'original_pinned_inputs',
        'scope': 'Explicit workflow obligations; correct values do not certify required files, table materialization, source-query ordering or repeated-build behavior.'}
    dest = batch / 'reconciliation.json'
    assert not dest.exists()
    write_json(dest, result)
    print({k: v for k, v in result.items() if k != 'trials'})
    return 0 if result['complete'] and not any(t['outcome'] == 'unresolved_execution' for t in trials) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    raise SystemExit(reconcile(parser.parse_args().batch.resolve()))
