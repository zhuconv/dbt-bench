"""RFM metrics, NTILE group allocation and priority from original raw inputs."""
from collections import Counter,defaultdict
from decimal import Decimal as D
import math
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import COLS,segment
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMERIC=INTS+('FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})'for p in range(1,39)for s in range(p+1))
TEXT={'customer_id','customer_name','rfm_score','rfm_segment'}
def ck(status,reason,**kw):return dict(status=status,reason=reason,**kw)
def num(x):return type(x)in(int,float,D)and D(str(x)).is_finite()
def same(a,e):return num(a)and(D(str(a))==D(e)or type(a)is float and abs(D.from_float(a)-D(e))<=D.from_float(math.ulp(float(e))))
def integral(x):return num(x)and D(str(x))==D(str(x)).to_integral_value()
def ntile_groups(rows,metric,score):
    """Either direct NTILE or reversed NTILE is legal; ties can use any total secondary order."""
    n=len(rows);q,rem=divmod(n,5);buckets=[b for b in range(1,6)for _ in range(q+(b<=rem))]
    for inverted in [False,True]:
        # Recency quality increases as days decrease; other metric quality increases with value.
        descending=(metric=='recency_days') ^ inverted;ordered=sorted(rows,key=lambda r:D(str(r[metric])),reverse=descending);wanted=defaultdict(Counter);actual=defaultdict(Counter)
        for r,b in zip(ordered,buckets):wanted[D(str(r[metric]))][6-b if inverted else b]+=1
        for r in rows:actual[D(str(r[metric]))][int(r[score])]+=1
        if wanted==actual:return True
    return False
def evaluate(outputs,expected,workflow=None):
    checks={};want=expected['customers'];maps={}
    for name,cols in COLS.items():
        o=outputs.get(name,{});rows=o.get('rows');policy=TablePolicy(TableContract(tuple(Column(c,'text'if c in TEXT else'numeric')for c in cols),keys=('customer_id',),allow_extra_columns=True),{c:('VARCHAR',)if c in TEXT else NUMERIC for c in cols},not_null=tuple(cols)if name=='rfm_segments'else('customer_id',))
        if o.get('exists')is False:checks[name+'.presence']=ck('fail','Required relation is actually absent')
        if o.get('table')is not None:
            result=audit_table(o['table'],policy);checks[name+'.interface']=ck('pending'if result['status']=='unknown'else'pass'if result['reward']==1 else'fail','Required columns/logical types and customer grain; no invented column order or materialization',details=result)
        else:checks[name+'.interface']=ck('pending','Exact physical interface capture unavailable')
        if rows is None:checks[name+'.values']=ck('pending','Rows unavailable');continue
        got={r.get('customer_id'):r for r in rows};maps[name]=got;checks[name+'.population']=ck('pass'if len(got)==len(rows)and set(got)==set(want)else'fail','All and only customers with qualifying original orders, including orphan customer IDs')
        errors=[]
        for cid,e in want.items():
            if cid not in got:continue
            r=got[cid]
            for c in ['frequency','recency_days']:
                if not integral(r.get(c))or not same(r[c],e[c]):errors.append([cid,c,str(r.get(c)),str(e[c])])
            if not any(same(r.get('monetary'),v)for v in e['monetary']):errors.append([cid,'monetary',str(r.get('monetary')),str(e['monetary'])])
            if name=='rfm_segments'and e['customer_name']is not None and r.get('customer_name')!=e['customer_name']:errors.append([cid,'customer_name',str(r.get('customer_name')),e['customer_name']])
        checks[name+'.metrics']=ck('fail'if errors else'pass','Independent raw recency, count and nearest-cent sum; matched customer names only',error_count=len(errors),examples=errors[:12])
        if name in ['rfm_scores','rfm_segments']:
            valid=all(all(integral(r.get(c))and 1<=r[c]<=5 for c in ['r_score','f_score','m_score'])for r in rows)
            checks[name+'.score_domain']=ck('pass'if valid else'fail','All dimension scores are finite integers 1..5')
            for metric,score in [('recency_days','r_score'),('frequency','f_score'),('monetary','m_score')]:
                ok=valid and all(num(r.get(metric))for r in rows)and ntile_groups(rows,metric,score)
                checks[name+'.'+score]=ck('pass'if ok else'fail','NTILE(5) bucket populations and primary metric order; every tied group may use any deterministic secondary ordering')
            if name=='rfm_segments':
                errors=[]
                for r in rows:
                    if not all(integral(r.get(c))and 1<=r[c]<=5 for c in ['r_score','f_score','m_score']):continue
                    rr,f,m=[int(r[c])for c in ['r_score','f_score','m_score']]
                    if r.get('rfm_score')!=f'{rr}{f}{m}':errors.append([r.get('customer_id'),'rfm_score'])
                    if r.get('rfm_segment')!=segment(rr,f,m):errors.append([r.get('customer_id'),'priority_segment'])
                checks['segments']=ck('fail'if errors else'pass','Independent first-match priority and string concatenation from actual valid scores',examples=errors[:12])
    if 'rfm_scores'in maps and'rfm_segments'in maps:
        errors=[cid for cid,r in maps['rfm_scores'].items()if cid in maps['rfm_segments']and any(r.get(c)!=maps['rfm_segments'][cid].get(c)for c in ['r_score','f_score','m_score'])];checks['score_propagation']=ck('fail'if errors else'pass','Final segmentation retains its actual intermediate scores',examples=errors[:12])
    if expected['facts']['unmatched_customers']:checks['orphan_name_policy']=ck('gap','Five qualifying IDs have no source customer row; all-row/no-NULL requirements are explicit, but the substitute concatenated name is not specified',ids=expected['facts']['unmatched_customers'])
    checks['workflow']=workflow or ck('pending','Trusted workflow and deterministic window evidence unavailable');states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if any(s in states for s in ['pending','gap'])else 1
    return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
