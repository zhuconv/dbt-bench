"""Bind ROI's actual run then test to independent full-input audit requests."""
from pathlib import Path
from collections import Counter
import json
import sys
import argparse

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(ROOT / 'scripts'), str(ROOT / 'src')]
from dbt_bench import io
from phase3_capture import verify_trial, timestamp
from audit_tables import decode
from check_v5 import run, inputs_from_capture

KEY = 'deb__dbt-retail-task-04-market-roi'
MODEL = 'rpt_channel_roi_payback_monthly'
RELATIONS = {'main__stg_fact_marketing_spend': 'marketing', 'main__stg_analytics__dim_date': 'dates',
    'main__stg_analytics__fact_sales': 'sales', 'main__stg_analytics__dim_customer': 'customers',
    'main__stg_analytics__dim_channel': 'channels'}


def save(path, value):
    if path.exists():
        raise ValueError('Preserve prior review')
    io.write_json(path, json.loads(json.dumps(value, default=str, allow_nan=False)))
    return io.evidence_ref(path, ROOT)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', required=True, type=Path)
    p.add_argument('--parsed', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    parsed = io.read_json(a.parsed)
    policy = io.read_json(HERE / 'policy.json')
    assert io.evidence_ok(policy['independent_inputs'], ROOT)
    original = io.read_json(ROOT / policy['independent_inputs']['path'])
    expected_inputs = inputs_from_capture(original, decode)
    records = []
    for result in sorted(a.batch.glob('jobs/*/*/result.json')):
        r = io.read_json(result)
        if Path(r['config']['task']['path']).name != KEY + '--phase3-blind-v1':
            continue
        trial = result.parent
        v = verify_trial(a.batch, trial)
        e, cap = v['execution'], v['capture']
        assert r['verifier_result']['rewards'] == {'reward': 1.0} and not r['exception_info']
        d = a.output / trial.name
        d.mkdir()
        initial_hash = next(x['sha256'] for x in e['initial_input_hashes'] if x['path'] == '/app/database/retail.duckdb')
        assert initial_hash == original['initial_database_sha256']
        raw_matches = {}
        for name, export in v['initial']['raw'].items():
            columns = [c.lower() for c in export['columns']]
            rows = [dict(zip(columns, map(decode, json.loads(line)))) for line in cap.runtime(export).read_text().splitlines()]
            prior = expected_inputs[RELATIONS[name]]
            names = sorted(prior[0])
            bag = lambda values: Counter(tuple((type(row[n]).__name__, row[n]) for n in names) for row in values)
            raw_matches[name] = bag(rows) == bag(prior)
        assert len(raw_matches) == 5 and all(raw_matches.values())
        build = next(c for c in v['commands'] if 'run' in c['run']['command'])
        test = next(c for c in v['commands'] if 'test' in c['run']['command'])
        observed_ast = [x for x in parsed['records'] if x['trial'] == str(trial)]
        assert len(observed_ast) == 1 and observed_ast[0]['manifest'] == io.evidence_ref(build['manifest_path'], ROOT)
        node = build['manifest']['nodes'][observed_ast[0]['unique_id']]
        assert node['name'] == MODEL
        parsed_ref = save(d / 'parsed.json', {'manifest_sha256': io.file_hash(build['manifest_path']),
            'parsed_compiled_sql': {MODEL: {'compiled_sql_sha256': observed_ast[0]['compiled_sha256'], 'ast': observed_ast[0]['ast']}},
            'parser_execution': io.evidence_ref(a.parsed, ROOT)})
        stages, bags = [], []
        for part in ('agent/audit-initial', 'agent/audit-submission', 'verifier/mechanical-before', 'verifier/mechanical-after'):
            snapshot_path = trial / part / 'snapshot.json'
            snap = cap.read(snapshot_path)
            stage = snap['stage']
            initial = stage == 'initial'
            files = v['initial_files'] if initial else v['final_files']
            inventory = e['initial_trees'] if initial else e['final_trees']
            trees = {x['relative_path']: x for x in inventory}
            project = files['dbt_project.yml']
            import yaml
            model_paths = yaml.safe_load(project.read_text()).get('model-paths', ['models'])
            sources = []
            for prefix in model_paths:
                tree = trees[prefix]
                assert not tree.get('error')
                for item in tree['files']:
                    name = prefix + '/' + item['path']
                    if name.endswith('.sql'):
                        entry = {'original_file_path': name}
                        if name in files:
                            assert io.file_hash(files[name]) == item['sha256']
                            entry['file'] = io.evidence_ref(files[name], ROOT)
                        sources.append(entry)
            sd = d / stage
            sd.mkdir()
            project_ref = save(sd / 'project.json', {'model_paths': model_paths, 'source': io.evidence_ref(project, ROOT)})
            observed = {name: {'exists': name in files, 'file': io.evidence_ref(files[name], ROOT) if name in files else None}
                        for name in set(v['request']['capture_files'])}
            facts = {'producer': 'locked_qwen_clean_rebuild', 'discarded_candidate_dbt_artifacts': True,
                'task_key': KEY, 'snapshot': io.evidence_ref(snapshot_path, ROOT), 'project': project_ref,
                'project_source': io.evidence_ref(project, ROOT),
                'profile_source': io.evidence_ref(files['profiles.yml'], ROOT) if 'profiles.yml' in files else None,
                'project_root': v['request']['project_root'], 'actual_database': '/app/database/retail.duckdb',
                'profile_environment': e['environment'], 'environment': e['environment'],
                'observed_files': observed, 'sources': sources, 'complete_model_inventory': True,
                'initial_required_models_absent': initial and not any(Path(s['original_file_path']).stem == MODEL for s in sources),
                'backend_shell': io.evidence_ref(v['backend_shell_path'], ROOT),
                'source_install_after_utc': e['initial_capture_hooks'][0]['finished_at_utc'],
                'trust_evidence': [*cap.evidence.values(), io.evidence_ref(Path(__file__), ROOT), io.evidence_ref(ROOT / 'scripts/phase3_capture.py', ROOT)]}
            if not initial:
                for f in v['request']['submission']['files']:
                    observed_file = next(x for x in snap['files'] if x['path'] == v['request']['project_root'] + '/' + f['path'])
                    assert observed_file['exists'] and observed_file['sha256'] == f['sha256']
                def invocation(command):
                    run = command['run']
                    return {'argv': run['command'], 'cwd': run['cwd'], 'returncode': run['returncode'],
                        'started_unix': timestamp(run['started_at_utc']), 'finished_unix': timestamp(run['finished_at_utc']),
                        'log': io.evidence_ref(trial / 'agent/audit-rebuild' / run['log'], ROOT),
                        'manifest': io.evidence_ref(command['manifest_path'], ROOT), 'run_results': io.evidence_ref(command['results_path'], ROOT)}
                facts.update(manifest=io.evidence_ref(build['manifest_path'], ROOT), run_results=io.evidence_ref(build['results_path'], ROOT),
                             invocation=invocation(build), required_test_execution=invocation(test))
            wf = save(sd / 'workflow.json', facts)
            request = {'task_key': KEY, 'snapshot': io.evidence_ref(snapshot_path, ROOT), 'stage': stage,
                       'workflow': wf, 'parsed_snapshot': parsed_ref, 'initial_database_sha256': initial_hash}
            sr = save(sd / 'request.json', request)
            answer = run(request, ROOT)
            ar = save(sd / 'audit.json', answer)
            stages.append({'stage': stage, 'request': sr, 'audit': ar, 'reward': answer['reward'], 'components': answer['components']})
            print(trial.name, stage, answer['reward'], flush=True)
            if not initial:
                out = next(x for x in snap['outputs'] if x['specification']['relation'] == 'main.' + MODEL)
                assert not out.get('error')
                path = snapshot_path.parent / out['file']
                assert io.file_hash(path) == out['sha256']
                bags.append((out['schema'], sorted(path.read_text().splitlines())))
        assert bags[0] == bags[1] == bags[2]
        records.append({'trial': str(trial), 'stages': stages, 'raw_matches': raw_matches,
            'context_values_equal': True, 'canonical_output_digest': io.digest(bags[0]), 'evidence': list(cap.evidence.values()),
            'capture_limitation': 'Final test manifest omits compiled_code; preceding actual run hook and exact run manifest supply it. Final physical output and native snapshots remain complete, and original KeyError capture retained.'})
    assert len(records) == 2 and len({r['canonical_output_digest'] for r in records}) == 1
    print(save(a.output / 'review.json', {'task_key': KEY, 'records': records, 'model_requests': 0,
        'code': [io.evidence_ref(p, ROOT) for p in HERE.glob('*.py')] + [io.evidence_ref(HERE / 'policy.json', ROOT)], 'native_rewards_modified': False}))


if __name__ == '__main__':
    main()
