"""Actual consecutive-build equality on explicitly required output projections."""
from collections import Counter
import json
from dbt_bench import io


def compare(verified, columns_by_model):
    builds=[c for c in verified['commands'] if c['run']['command'][:2] in (['dbt','run'],['dbt','build']) and '--project-dir' not in c['run']['command']]
    if len(builds)<2:return {'status':'gap','reward':None,'reason':'Declared same-workspace idempotence requires two observed builds.'}
    pairs=builds[-2:];captures=[]
    for build in pairs:
        hooks=build['hooks']
        if len(hooks)!=1:return {'status':'gap','reward':None,'reason':'Each actual build requires its own complete output capture.'}
        captures.append(hooks[0])
    comparisons=[]
    for name,columns in columns_by_model.items():
        values=[];refs=[];schemas=[]
        for capture in captures:
            table=capture.get('physical_outputs',{}).get(name)
            if table is None:return {'status':'gap','reward':None,'reason':'Idempotence output missing from actual capture: '+name}
            names=[n.lower() for n in table['columns']]
            if len(names)!=len(set(names)) or not set(columns)<=set(names):return {'status':'gap','reward':None,'reason':'Required output projection not present in capture: '+name}
            path=verified['capture'].runtime(table);indices=[names.index(n)for n in columns]
            values.append(Counter(tuple(json.dumps(json.loads(line)[i],sort_keys=True,separators=(',',':'))for i in indices)for line in path.read_text().splitlines()))
            refs.append(io.evidence_ref(path,verified['capture'].root));types={r[0].lower():r[1]for r in table['sql_schema']};schemas.append([types[n]for n in columns])
        comparisons.append({'model':name,'columns':list(columns),'same_typed_multiset':values[0]==values[1],'same_sql_types':schemas[0]==schemas[1],'evidence':refs})
    ok=all(c['same_typed_multiset']and c['same_sql_types']for c in comparisons)
    return {'status':'pass'if ok else'fail','reward':1 if ok else 0,'comparisons':comparisons,
        'reason':'Actual consecutive reconstructed builds on unchanged fixed inputs; only declared contract columns compared, row order ignored and duplicate multiplicity retained.',
        'scope':'Submitted-source idempotence, not evidence the original model ran dbt twice.'}
