"""Recheck unchanged historical captures with v2, preserving previous scores."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from checker import score

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(HERE.parent / 'audit_v1'))
from capture import verify_capture
from dbt_bench.io import evidence_ref, evidence_ok, read_json, write_json


def run(out):
    out.mkdir(parents=True, exist_ok=False)
    previous_path = ROOT / 'artifacts/validation/workday-retained-audit-v2/summary.json'
    previous = read_json(previous_path)
    originals = {}
    for reference in previous['source_reconciliations']:
        assert evidence_ok(reference, ROOT)
        for trial in read_json(ROOT / reference['path'])['trials']:
            assert trial['trial_id'] not in originals
            originals[trial['trial_id']] = trial
    records = []
    for entry in previous['records']:
        if entry['audit'] is None:
            records.append({'trial_id': entry['trial_id'], 'status': 'not_evaluated'})
            continue
        verify_capture(ROOT, originals[entry['trial_id']])
        assert evidence_ok(entry['input_manifest'], ROOT) and evidence_ok(entry['audit'], ROOT)
        result = score(ROOT, ROOT / entry['input_manifest']['path'])
        old = read_json(ROOT / entry['audit']['path'])
        # Persisted JSON represents internal tuple keys as arrays.
        comparable = json.loads(json.dumps(result))
        for key in ['status', 'audit_reward', 'data', 'workflow', 'input_profile', 'data_scope']:
            assert comparable[key] == old[key], (entry['trial_id'], key)
        destination = out / 'audits' / (entry['trial_id'] + '.json')
        write_json(destination, result)
        records.append(dict(trial_id=entry['trial_id'], status=result['status'],
            audit=evidence_ref(destination, ROOT), previous_audit=entry['audit'],
            input_manifest=entry['input_manifest']))
    summary = dict(schema_version=1, previous=evidence_ref(previous_path, ROOT),
        scoring_code=evidence_ref(HERE / 'checker.py', ROOT),
        runner=evidence_ref(Path(__file__), ROOT), records=records,
        statuses=dict(Counter(r['status'] for r in records)), evaluated=25,
        new_benchmark_trials=0, new_model_trials=0, certified=False,
        scope='Retained schema-v1 captures revalidated for provenance and score parity under v2; this does not retroactively add pre/post-native capture to old trials.')
    assert len(records) == 26 and summary['statuses'] == {'pass': 4, 'fail': 14, 'unknown': 7, 'not_evaluated': 1}
    write_json(out / 'summary.json', summary)
    print(summary['statuses'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    run(parser.parse_args().output_dir.resolve())
