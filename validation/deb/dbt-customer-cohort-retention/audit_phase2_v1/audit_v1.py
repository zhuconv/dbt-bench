"""Cohort contracts with exact source arithmetic and explicit display rounding paths."""
from collections import defaultdict
from decimal import Decimal as D
from fractions import Fraction
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import COLS,cents
COUNTS={'months_since_first_order','cohort_size','retained_customers','cumulative_retained','months_active'}
RATES={'retention_rate','cumulative_retention_rate','retention_month_6','retention_month_12'}
DECIMALS=tuple(f'DECIMAL({p},{s})'for p in range(1,39)for s in range(p+1))
def ck(status,reason,**kw):return dict(status=status,reason=reason,**kw)
def finite(x):return type(x)in(int,float,D)and D(str(x)).is_finite()
def eq(a,e):return a in e if isinstance(e,list)else type(a)is type(e)and a==e
def key(r,name):return(r.get('cohort_month'),)if name=='cohort_summary'else(r.get('cohort_month'),r.get('months_since_first_order'))
def evaluate(outputs,expected,workflow=None):
    checks={};maps={};want={}
    for name,cols in COLS.items():
        o=outputs.get(name,{});rows=o.get('rows');policy=TablePolicy(TableContract(tuple(Column(c,'text'if c=='cohort_month'else'integer'if c in COUNTS else'numeric')for c in cols),keys=('cohort_month',)if name=='cohort_summary'else('cohort_month','months_since_first_order'),allow_extra_columns=True),{c:('VARCHAR',)if c=='cohort_month'else('INTEGER',)if c in COUNTS else('DECIMAL(5,2)',)if c in RATES else DECIMALS for c in cols},not_null=tuple(c for c in cols if c not in ['retention_month_6','retention_month_12']),relation_types=('TABLE','BASE TABLE'))
        if o.get('exists')is False:checks[name+'.presence']=ck('fail','Explicit required table absent')
        if o.get('table')is not None:
            result=audit_table(o['table'],policy);checks[name+'.interface']=ck('pending'if result['status']=='unknown'else'pass'if result['reward']==1 else'fail','Explicit columns/types/table/grain with no invented column order',details=result)
        else:checks[name+'.interface']=ck('pending','Exact physical interface not captured')
        if o.get('relation_type')is not None:checks[name+'.materialization']=ck('pass'if o['relation_type']in['TABLE','BASE TABLE']else'fail','Actual table relation kind')
        if rows is None:checks[name+'.values']=ck('pending','Rows unavailable');continue
        got={key(r,name):r for r in rows};maps[name]=got;required={key(r,name):r for r in expected['outputs'][name]};allowed=required|{key(r,name):r for r in expected['optional_zero_months'].get(name,[])};want[name]=allowed
        checks[name+'.population']=ck('pass'if len(got)==len(rows)and set(required)<=set(got)<=set(allowed)else'fail','All observed source cohort buckets required; zero-activity months within declared horizon permitted with exact zero-month semantics')
        errors=[];ignored={'revenue_per_customer','cumulative_revenue','cumulative_revenue_per_customer'}if name=='cohort_revenue'else set()
        for k,r in got.items():
            if k not in allowed:continue
            for c,e in allowed[k].items():
                if c not in ignored and not eq(r.get(c),e):errors.append([str(k),c,str(r.get(c)),str(e)])
        checks[name+'.values']=ck('fail'if errors else'pass','Independent source cohorts, distinct retention, first-month union, source monthly/lifetime revenue and observable month6/12 NULL policy',error_count=len(errors),examples=errors[:12])
        bad=[str(key(r,name))for r in rows if any(r.get(c)is not None and(not finite(r[c])or D(str(r[c]))!=D(str(r[c])).quantize(D('.01')))for c in cols if c not in COUNTS and c!='cohort_month')];checks[name+'.decimal_precision']=ck('fail'if bad else'pass','Every displayed numeric decimal has at most two fractional digits, finite; exact rational half-cent ties allow only nearest cents',examples=bad[:12])
    if 'cohort_revenue'in maps:
        got=maps['cohort_revenue'];running=defaultdict(lambda:D(0));errors={c:{p:[]for p in ['raw_final_rounding','displayed_monthly_rounding']}for c in ['revenue_per_customer','cumulative_revenue','cumulative_revenue_per_customer']}
        for k,r in sorted(got.items(),key=lambda kv:(str(kv[0][0]),kv[0][1]if type(kv[0][1])is int else -1)):
            if k not in want['cohort_revenue']:continue
            e=want['cohort_revenue'][k];amount=r.get('total_revenue');size=r.get('cohort_size')
            if not finite(amount)or type(size)is not int or size<=0:
                for c in errors:
                    for p in errors[c]:errors[c][p].append(str(k))
                continue
            running[k[0]]+=D(str(amount));display={'revenue_per_customer':cents(Fraction(D(str(amount)))/size),'cumulative_revenue':[running[k[0]]],'cumulative_revenue_per_customer':cents(Fraction(running[k[0]])/size)}
            for c in errors:
                if not eq(r.get(c),e[c]):errors[c]['raw_final_rounding'].append(str(k))
                if not eq(r.get(c),display[c]):errors[c]['displayed_monthly_rounding'].append(str(k))
        for c,policies in errors.items():
            modes=[p for p,bad in policies.items()if not bad];checks['cohort_revenue.'+c]=ck('pass'if modes else'fail','Exact source-final-rounding OR exact independently valid displayed-month arithmetic, one consistent path per output column; never generic numerical tolerance',accepted_paths=modes,error_counts={p:len(v)for p,v in policies.items()},examples={p:v[:6]for p,v in policies.items()})
    checks['workflow']=workflow or ck('pending','Trusted actual workflow unavailable');states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if any(s in states for s in ['pending','gap'])else 1
    return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
