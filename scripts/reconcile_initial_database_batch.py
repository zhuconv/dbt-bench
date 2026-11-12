#!/usr/bin/env python3
"""Reconcile read-only initial DB probes without certifying visibility by name alone."""
import argparse
from pathlib import Path
from dbt_bench import io
from review_tools import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    assert not args.output.exists()
    manifest=io.read_json(args.batch/'manifest.json')
    assert io.evidence_ok(manifest['config'],ROOT)
    config=io.read_json(ROOT/manifest['config']['path'])
    assert config['verifier']['disable'] and config['n_attempts']==1
    job=Path(config['jobs_dir'])/config['job_name']
    assert io.read_json(job/'result.json')['finished_at']
    planned={str(Path(t['path']).resolve()):Path(t['path']).name for t in config['tasks']}
    ids,observations=set(),{}
    for path in sorted(job.glob('*/result.json')):
        raw=io.read_json(path)
        key=planned[str(Path(raw['config']['task']['path']).resolve())]
        assert raw['finished_at'] and raw['id'] not in ids and key not in observations
        ids.add(raw['id'])
        assert not raw.get('verifier_result')
        entry=manifest['tasks'][key]
        assert io.tree_hash(Path(raw['config']['task']['path']))==entry['task_hash']
        data_path=path.parent/'agent/initial-databases.json'
        shell_path=path.parent/'agent/initial-databases-shell.json'
        no_model_path=path.parent/'agent/no-model.json'
        data=io.read_json(data_path) if data_path.exists() else None
        shell=io.read_json(shell_path) if shell_path.exists() else None
        no_model=io.read_json(no_model_path) if no_model_path.exists() else None
        if no_model:
            assert no_model['model_requests']==0 and no_model['hidden_material_uploaded'] is False
        observed={'group_task_key':key,'trial_id':raw['id'],'exception_info':raw.get('exception_info'),
            'result':io.evidence_ref(path,ROOT),'native_reward':None,'model_requests':0,
            'probe_executed':bool(data and shell and no_model),'shell_exit':shell['exit_code'] if shell else None,
            'metadata':io.evidence_ref(data_path,ROOT) if data else None,
            'shell':io.evidence_ref(shell_path,ROOT) if shell else None,
            'image':entry['image'],'task_bindings':entry['task_bindings'],
            'database_facts':[],'hidden_material_assessment':'pending_task_review'}
        if data:
            assert data['model_requests']==0 and data['hidden_material_uploaded'] is False
            for database in data['databases']:
                observed['database_facts'].append({'path':database['database'],'exists':database['exists'],
                    'unchanged':database.get('unchanged'),'error':database.get('error'),
                    'before_sha256':database.get('before_sha256'), 'after_sha256':database.get('after_sha256'),
                    'relation_count':len(database['relations']),'view_count':len(database['views'])})
        observations[key]=observed
        for bound in entry['task_bindings']:
            assert io.evidence_ok(bound['binding'],ROOT)
            io.write_json(args.output/bound['task_key']/'initial-database.json',
                          {**observed,'task_key':bound['task_key'],'binding':bound['binding']})
    assert set(observations)==set(manifest['tasks'])
    summary={'schema_version':1,'planned_trials':manifest['planned_trials'],'observed_trials':len(ids),
        'candidate_count':manifest['candidate_count'],'exceptions':sum(bool(v['exception_info']) for v in observations.values()),
        'all_probes_executed':all(v['probe_executed'] and v['shell_exit']==0 for v in observations.values()),
        'all_databases_unchanged':all(d['unchanged'] is True and d['exists'] and not d['error'] for v in observations.values() for d in v['database_facts']),
        'manifest':io.evidence_ref(args.batch/'manifest.json',ROOT),'job_result':io.evidence_ref(job/'result.json',ROOT),
        'records':[io.evidence_ref(p,ROOT) for p in sorted(args.output.glob('*/initial-database.json'))],
        'new_model_requests':0,'native_rewards_modified':False,'canonical_modified':False,
        'scope':'Actual initial database metadata. Each task still requires interpretation against declared inputs and hidden material; names alone do not prove a leak.',
        'reconciler':io.evidence_ref(Path(__file__),ROOT)}
    io.write_json(args.output/'summary.json',summary)
    print({k:summary[k] for k in ('planned_trials','observed_trials','candidate_count','exceptions','all_probes_executed','all_databases_unchanged')})


if __name__=='__main__':main()
