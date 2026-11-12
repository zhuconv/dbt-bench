"""Review separately labelled additional-data fixtures; never assign native scores."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(ROOT/'src')]
import argparse
from collections import Counter
from decimal import Decimal
import json
import shutil
import yaml
import audit
from dbt_bench.io import evidence_ref, evidence_ok, file_hash, tree_hash, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    base, output = args.job_dir.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    plan = json.loads((base/'manifest.json').read_text())
    config = json.loads((base/'config.yaml').read_text())
    assert plan['additional_input_fixture'] and config['verifier']['disable']
    assert evidence_ok(plan['config'], ROOT)
    mapping = {p['task_path']: p for p in plan['planned']}
    counts = Counter()
    records = []
    code = [HERE/'audit.py', HERE/'independent.py', HERE/'review_tiny.py', ROOT/'src/dbt_bench/comparison.py']
    (output/'checker-source').mkdir()
    for path in code:
        shutil.copy2(path, output/'checker-source'/path.name)
    for p in sorted((base/'jobs'/config['job_name']).glob('*/result.json')):
        native = json.loads(p.read_text())
        entry = mapping[native['config']['task']['path']]
        assert evidence_ok(entry['manifest'], ROOT)
        manifest = json.loads((ROOT/entry['manifest']['path']).read_text())
        assert tree_hash(Path(entry['task_path'])) == manifest['derived_task_hash']
        name = entry['case']
        counts[name] += 1
        assert native['verifier_result'] is None and native['exception_info'] is None
        proof_path = p.parent/'agent/case-proof.json'
        proof = json.loads(proof_path.read_text())
        expected_validity = 'built_from_inputs' if name == 'correct' else 'business_error'
        assert proof['validity'] == expected_validity and not proof['tested_checker_invoked_to_define_validity']
        directory = p.parent/'artifacts'/manifest['snapshot_container_path'].lstrip('/')
        load = lambda name: json.loads((directory/name).read_text(), parse_float=Decimal)
        required = ['marketing.json','dates.json','sales.json','customers.json','channels.json',
                    'output.json','submission.sql','submission.yml','dbt-manifest.json']
        assert not audit.validate_snapshot(directory, load('snapshot.json'), required)
        outcome = audit.evaluate({n: load(n+'.json') for n in ['marketing','dates','sales','customers','channels']},
                                 load('output.json'), (directory/'submission.sql').read_text(),
                                 yaml.safe_load((directory/'submission.yml').read_text()), load('dbt-manifest.json'))
        records.append({'case':name,'fixture_only':True,'native_reward':None,
                        'expected_acceptance': name == 'correct', 'audit_core_result': outcome,
                        'matches_independent_expectation':outcome['success'] == (name == 'correct'),
                        'proof':evidence_ref(proof_path,ROOT),'native_trial':evidence_ref(p,ROOT),
                        'snapshot':evidence_ref(directory/'snapshot.json',ROOT),
                        'inputs_outputs': [evidence_ref(directory/f,ROOT) for f in required]})
    expected = Counter(p['case'] for p in plan['planned'])
    complete = counts == expected and len(records) == len(plan['planned']) and all(r['matches_independent_expectation'] for r in records)
    report = {'scope':'Additional tiny warehouse, real dbt builds; tests audit semantic/artifact functions, not production fixed-input admission or original-data native grader',
              'benchmark_trials':0, 'additional_fixture_trials':len(records), 'qwen_smoke':False,
              'native_rewards_all_null':True, 'complete':complete, 'checker_certified':False,
              'expected':dict(expected),'observed':dict(counts),'trials':records,
              'code':[evidence_ref(output/'checker-source'/p.name,ROOT) for p in code],
              'plan':evidence_ref(base/'manifest.json',ROOT),'config':evidence_ref(base/'config.yaml',ROOT)}
    write_json(output/'summary.json',report)
    print(json.dumps({k:report[k] for k in ['benchmark_trials','additional_fixture_trials','complete','native_rewards_all_null']}))
    return int(not complete)


if __name__ == '__main__':
    raise SystemExit(main())
