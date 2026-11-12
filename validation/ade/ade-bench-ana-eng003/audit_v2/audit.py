"""Versioned customer audit: unchanged v1 workflow/data checks plus declared output schema.

Certification is recorded separately in contract.yaml. V1 scores are immutable.
This checker only reads already collected before/after-native-build snapshots.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
TASK=HERE.parent
ROOT=TASK.parents[2]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.io import evidence_ref,file_hash,read_json,write_json

spec=importlib.util.spec_from_file_location('customer_audit_v1_base',TASK/'audit.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)


def code_files():
    return [Path(__file__),HERE/'policy.json',TASK/'audit.py',TASK/'collect_audit.py',ROOT/'src/dbt_bench/comparison.py']


def version():
    return 'customer-output-audit-v2:'+hashlib.sha256(''.join(file_hash(p) for p in code_files()).encode()).hexdigest()


def output_interface(output):
    policy=read_json(HERE/'policy.json')
    expected=set(policy['required_columns'])
    if expected!=set(base.COLUMNS) or policy['allow_added_output_columns'] is not False:
        raise ValueError('Output policy differs from the declared customer transformation')
    columns=output.get('columns',[])
    if not isinstance(columns,list) or any(not isinstance(c,str) for c in columns):
        raise ValueError('Malformed output column inventory')
    missing=sorted(expected-set(columns));extra=sorted(set(columns)-expected)
    duplicates=len(columns)!=len(set(columns))
    rows=output.get('rows')
    if not isinstance(rows,list) or any(not isinstance(row,dict) or set(row)!=set(columns) for row in rows):
        raise ValueError('Export rows and column inventory disagree')
    return {'pass':not missing and not extra and not duplicates,
            'missing_columns':missing,'extra_columns':extra,'duplicate_column_names':duplicates,
            'policy_version':policy['version']}


def score(directory):
    directory=Path(directory)
    result=base.score(directory)
    post=base.verified_snapshot(directory/'after-native-build','after-native-build')
    if post['files'].get('output.json',{}).get('absent'):
        interface={'pass':False,'reason':'Required output relation absent'}
    else:
        interface=output_interface(read_json(directory/'after-native-build/output.json'))
    return {**result,'success':result['success'] and interface['pass'],'output_interface':interface}


def evaluate(directory):
    directory=Path(directory)
    result={'checker_version':version(),'checker_validation':'consult current contract.yaml',
            'status':'error','reward':None,'success':None,
            'code':[evidence_ref(p,ROOT) for p in code_files()],
            'snapshots':[evidence_ref(directory/phase/'snapshot.json',ROOT)
                         for phase in ['before','after-native-build'] if (directory/phase/'snapshot.json').is_file()],
            'scope':'Fixed input, unchanged v1 source/build/data checks and the versioned customer output-interface policy. No dbt invocation, original-score substitution or automatic certification.'}
    try:
        result.update(score(directory));result.update(status='scored',reward=float(result['success']))
    except (OSError,ValueError,KeyError,TypeError,ArithmeticError) as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Preserve previous audit results')
    result=evaluate(args.snapshot);write_json(args.output,result)
    print(json.dumps({k:result[k] for k in ['status','reward','checker_version']}))
    return 0 if result['status']=='scored' else 2


if __name__=='__main__':raise SystemExit(main())
