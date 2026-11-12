"""Fraud audit with runtime-resolved selected profile; original audit retained."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import yaml

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / 'audit_phase2_v1'
sys.path.insert(0, str(OLD))
import run_audit_v4 as legacy
import workflow_v3

PROFILE = HERE.parents[1] / 'dbt-customer-risk-scoring/phase4_v1/profile_runtime_v2.py'
s = importlib.util.spec_from_file_location('profile_runtime', PROFILE)
profile = importlib.util.module_from_spec(s)
s.loader.exec_module(profile)


def workflow(kind, facts):
    result = workflow_v3.evaluate(kind, facts)
    if not facts.get('trusted_process_complete'):
        return result
    try:
        files = facts['files']; env = facts['process']['environment']
        config = yaml.safe_load(files['dbt_project.yml'])
        selected = yaml.safe_load(files['profiles.yml'])['retail_dw_master']
        name = profile.scalar(selected['target'], env)
        target = {k: profile.scalar(v, env) for k, v in selected['outputs'][name].items()}
        ok = (profile.scalar(config['profile'], env) == 'retail_dw_master'
              and target['type'] == 'duckdb' and target['path'] == '/app/database/retail.duckdb'
              and env['DBT_PROFILES_DIR'] == workflow_v3.PROJECT)
        p = {'status': 'pass' if ok else 'fail',
             'reason': 'Selected original profile resolves against captured actual process environment; declared profile/database/location preserved',
             'selected_target': name, 'resolved_path': target.get('path')}
    except profile.UnresolvedProfile as exc:
        p = {'status': 'pending', 'reason': str(exc)}
    except (KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        p = {'status': 'fail', 'reason': 'Invalid captured selected profile: ' + str(exc)}
    result['details']['profile'] = p
    states = [v['status'] for v in result['details'].values()]
    result['status'] = 'fail' if 'fail' in states else 'pending' if 'pending' in states else 'gap' if 'gap' in states else 'pass'
    return result


def run(spec, root):
    legacy.workflow_evaluate = workflow
    result = legacy.run(spec, root)
    result['checker_version'] = 'phase4-fraud-runtime-profile-v1'
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--spec', required=True)
    p.add_argument('--output', required=True); p.add_argument('--root', default='.')
    a = p.parse_args(); result = run(json.loads(Path(a.spec).read_text()), Path(a.root))
    Path(a.output).write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'audit_reward': result['reward']}))
