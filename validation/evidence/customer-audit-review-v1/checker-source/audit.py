"""Experimental read-only customer audit; certification is in contract.yaml.

The original verifier's setup builds the same submitted dbt sources, then a
collector exports its output before hidden seeds/tests are installed. Source
and initial-data identities must agree before/after the build. Native scores
are never read to decide this audit's result and never modified.
"""
from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
import argparse,hashlib,json,sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.comparison import Column,TableContract,compare_tables
from dbt_bench.io import evidence_ref,file_hash,write_json

INPUT_SHA='101e6802bd656c05c8f9f1b04b6b8ea1677d1ae79645507acd10dbf5a7b8f106'
ATTRIBUTES=('company','last_name','first_name','email_address','job_title',
            'business_phone','home_phone','mobile_phone','fax_number','address',
            'city','state_province','zip_postal_code','country_region','web_page','notes','attachments')
COLUMNS=('customer_id',)+ATTRIBUTES
CONTRACT=TableContract(tuple(Column(c,'integer' if c in {'customer_id','zip_postal_code'} else 'text') for c in COLUMNS),
                       keys=('customer_id',),allow_extra_columns=True)


def expected_rows(source):
    """Independent source-key alignment; no case proof, gold or grader imports."""
    groups=defaultdict(list)
    for row in source:
        if set(row)!=set(ATTRIBUTES)|{'id'} or type(row['id']) is not int:
            raise ValueError('Unexpected fixed customer source schema/key')
        groups[row['id']].append(row)
    result=[]
    for key,rows in sorted(groups.items()):
        attrs={c:rows[0][c] for c in ATTRIBUTES}
        if any(any(r[c]!=attrs[c] or type(r[c]) is not type(attrs[c]) for c in ATTRIBUTES) for r in rows):
            raise ValueError('Conflicting duplicate source key requires adjudication')
        result.append({'customer_id':key,**attrs})
    return result


def normalize_rows(rows,types):
    """Integral numeric representations retain exact value; text IDs are not guessed."""
    result=[]
    for original in rows:
        row=dict(original)
        for c in ('customer_id','zip_postal_code'):
            value=row.get(c)
            if value is None:continue
            if isinstance(value,str) and types.get(c,'').startswith('DECIMAL('):value=Decimal(value)
            if isinstance(value,bool) or not isinstance(value,(int,float,Decimal)):
                raise ValueError('Non-numeric integer column: '+c)
            number=Decimal(str(value))
            if not number.is_finite() or number!=number.to_integral_value():
                raise ValueError('Non-integral integer column: '+c)
            row[c]=int(number)
        result.append(row)
    return result


def compare_output(source,output,types):
    if not set(COLUMNS)<=set(output.get('columns',[])):
        return {'equal':False,'reason':'Missing required output columns'}
    try:return asdict(compare_tables(expected_rows(source),normalize_rows(output['rows'],types),CONTRACT))
    except (ValueError,TypeError,ArithmeticError,KeyError) as exc:return {'equal':False,'reason':str(exc)}


def verified_snapshot(directory,phase):
    snapshot=json.loads((directory/'snapshot.json').read_text())
    if snapshot['phase']!=phase or not snapshot['read_only_database'] or snapshot['dbt_invoked'] or snapshot['errors']:
        raise ValueError('Invalid collection phase or collector failure')
    for name,ref in snapshot['files'].items():
        path=directory/name
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Unsafe export path')
        if ref.get('absent'):
            if path.exists():raise ValueError('Attested absent export exists')
        elif not path.is_file() or file_hash(path)!=ref['sha256']:
            raise ValueError('Export changed or missing: '+name)
    for name,digest in snapshot['project_files'].items():
        if snapshot['files'].get('project/'+name,{}).get('sha256')!=digest:
            raise ValueError('Project inventory inconsistent')
    return snapshot


def score(directory):
    pre_dir=directory/'before';post_dir=directory/'after-native-build'
    pre=verified_snapshot(pre_dir,'before');post=verified_snapshot(post_dir,'after-native-build')
    if pre['project_files']!=post['project_files']:
        raise ValueError('Native setup changed submitted sources; independent-copy evaluation required')
    if any(s['files'].get('input.json',{}).get('sha256')!=INPUT_SHA for s in [pre,post]):
        raise ValueError('Pinned initial input changed')
    source=json.loads((pre_dir/'input.json').read_text())['rows']
    if post['files'].get('manifest.json',{}).get('absent'):
        return {'success':False,'issues':['No manifest after native build']}
    manifest=json.loads((post_dir/'manifest.json').read_text())
    models=[(key,node) for key,node in manifest.get('nodes',{}).items()
            if node.get('resource_type')=='model' and node.get('name')=='dim_customer']
    issues=[]
    if len(models)!=1:
        return {'success':False,'issues':['Required dim_customer model missing or ambiguous']}
    key,node=models[0];path=node.get('original_file_path','')
    source_file=pre_dir/'project'/path
    if path not in pre['project_files'] or not source_file.is_file():issues.append('Built model has no captured submitted source')
    elif source_file.read_text().strip()!=(node.get('raw_code') or '').strip():issues.append('Built SQL differs from submitted source')
    if node.get('schema')!='main' or node.get('alias')!='dim_customer':issues.append('Built relation differs from native declared output')
    if post['setup_exit']!=0:issues.append('Native dbt setup did not complete successfully')
    if post['files'].get('run_results.json',{}).get('absent'):
        issues.append('Native build result missing')
    else:
        run=json.loads((post_dir/'run_results.json').read_text())
        prior=json.loads((pre_dir/'run_results.json').read_text()) if not pre['files'].get('run_results.json',{}).get('absent') else {}
        if run.get('metadata',{}).get('invocation_id')==prior.get('metadata',{}).get('invocation_id'):
            issues.append('No fresh native build invocation')
        if not any(r.get('unique_id')==key and r.get('status')=='success' for r in run.get('results',[])):
            issues.append('Requested model did not build successfully')
    if post['files'].get('output.json',{}).get('absent'):
        comparison={'equal':False,'reason':'Required relation absent after native build'}
    else:
        output=json.loads((post_dir/'output.json').read_text(),parse_float=Decimal)
        comparison=compare_output(source,output,post['files']['output.json']['types'])
    return {'success':comparison['equal'] and not issues,'comparison':comparison,'issues':issues,
        'source_rows':len(source),'expected_customers':len(expected_rows(source)),
        'submission_snapshot':evidence_ref(pre_dir/'snapshot.json',ROOT),
        'evaluated_output_snapshot':evidence_ref(post_dir/'snapshot.json',ROOT)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Preserve prior audit results')
    code=[Path(__file__),HERE/'collect_audit.py',ROOT/'src/dbt_bench/comparison.py']
    result={'checker_version':'customer-key-audit-v1:'+hashlib.sha256(''.join(file_hash(p) for p in code).encode()).hexdigest(),
        'checker_validation':'pending; consult contract.yaml','status':'error','reward':None,'success':None,
        'code':[evidence_ref(p,ROOT) for p in code],
        'scope':'Fixed input and captured source inventory; consumes unchanged native dbt setup output before hidden tests. No native reward substitution.'}
    result['snapshots']=[evidence_ref(args.snapshot/phase/'snapshot.json',ROOT)
                         for phase in ['before','after-native-build'] if (args.snapshot/phase/'snapshot.json').is_file()]
    try:
        result.update(score(args.snapshot));result.update(status='scored',reward=float(result['success']))
    except (ValueError,OSError,KeyError,TypeError,ArithmeticError) as exc:result['error']=f'{type(exc).__name__}: {exc}'
    write_json(args.output,result)
    print(json.dumps({k:result[k] for k in ['status','reward','checker_version']}))
    return 0 if result['status']=='scored' else 2


if __name__=='__main__':raise SystemExit(main())
