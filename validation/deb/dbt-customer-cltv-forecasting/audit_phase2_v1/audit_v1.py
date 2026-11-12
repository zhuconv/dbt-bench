"""CLTV defined arithmetic and counts; actual NULL-date conflicts remain explicit."""
from decimal import Decimal as D
from fractions import Fraction
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v2 import COLS,cents
NAME='rpt_customer_cltv_forecast';TEXT={'customer_id'};DATES={'as_of_date'};COUNTS={'current_order_count','days_since_last_order'};MONEY=set(COLS[NAME])-TEXT-DATES-COUNTS
def ck(status,reason,**kw):return dict(status=status,reason=reason,**kw)
def num(x):return type(x)in(int,float,D)and D(str(x)).is_finite()
def evaluate(outputs,expected,workflow=None):
    checks={};o=outputs.get(NAME,{});rows=o.get('rows');cols=COLS[NAME];policy=TablePolicy(TableContract(tuple(Column(c,'text'if c in TEXT else'date'if c in DATES else'integer'if c in COUNTS else'numeric')for c in cols),keys=('customer_id',),allow_extra_columns=True),{c:('VARCHAR',)if c in TEXT else('DATE',)if c in DATES else('INTEGER',)if c in COUNTS else('DECIMAL(18,2)',)for c in cols},not_null=('customer_id',),relation_types=('BASE TABLE','TABLE'))
    if o.get('exists')is False:checks['presence']=ck('fail','Required output is absent')
    if o.get('table')is not None:
        result=audit_table(o['table'],policy);checks['interface']=ck('pending'if result['status']=='unknown'else'pass'if result['reward']==1 else'fail','Explicit SQL types, actual TABLE and customer grain; column order not prescribed',details=result)
    else:checks['interface']=ck('pending','Exact physical interface unavailable')
    if o.get('relation_type')is not None:checks['table_materialization']=ck('pass'if o['relation_type']in('TABLE','BASE TABLE')else'fail','Independently captured relation type')
    if rows is not None:
        got={r.get('customer_id'):r for r in rows};known=expected['customers'];allcounts=expected['all_noncancelled_counts'];checks['population_known']=ck('pass'if len(got)==len(rows)and set(known)<=set(got)<=set(allcounts)else'fail','Known dated eligible population must exist; no cancelled-only/null/unknown customer. Undated-only population interpretation separate.')
        errors={k:[]for k in ['as_of','count','recency','aov','forecast','nonnegative_monotonic']}
        for cid,r in got.items():
            if r.get('as_of_date')!=expected['facts']['global_as_of_date']:errors['as_of'].append(cid)
            if cid in allcounts and(type(r.get('current_order_count'))is not int or r['current_order_count']!=allcounts[cid]):errors['count'].append([cid,r.get('current_order_count'),allcounts[cid]])
            if cid in known and r.get('days_since_last_order')!=known[cid]['days_since_last_order']:errors['recency'].append(cid)
            current=r.get('current_lifetime_revenue');count=r.get('current_order_count');moneyok=all(num(r.get(c))and D(str(r[c]))>=0 for c in MONEY)
            if not moneyok or not(r['forecast_3m_revenue']<=r['forecast_6m_revenue']<=r['forecast_12m_revenue']if moneyok else False):errors['nonnegative_monotonic'].append(cid)
            if num(current)and type(count)is int:
                if r.get('avg_order_value')not in cents(Fraction(D(str(current)))/max(count,1)):errors['aov'].append(cid)
                m=expected['monthly'].get(cid);avg=Fraction(m['average_numerator'],m['average_denominator'])if m else Fraction(0)
                for n in [3,6,12]:
                    targets=cents(Fraction(D(str(current)))+n*avg)
                    if r.get(f'forecast_{n}m_revenue')not in targets:errors['forecast'].append([cid,n,str(r.get(f'forecast_{n}m_revenue')),list(map(str,targets))])
        reasons={'as_of':'Maximum date of all original source rows','count':'Distinct order_id for all non-cancelled non-NULL customer rows; no date filter stated for this count','recency':'Global as-of minus latest known source date','aov':'Reported current revenue/max(reported count,1), with only exact final half-cent ties admitting either nearest cent','forecast':'Reported current revenue plus n times independent exact six-full-month average; premature average rounding invalid independently of undated lifetime membership','nonnegative_monotonic':'Money fields nonnegative and forecasts monotone'}
        for k,err in errors.items():checks[k]=ck('fail'if err else'pass',reasons[k],error_count=len(err),examples=err[:12])
        total=sum((D(str(r['current_lifetime_revenue']))for r in rows if num(r.get('current_lifetime_revenue'))),D(0));checks['reconciliation_policy']=ck('gap','Actual NULL-date amounts and positive NULL-customer revenue: exclude-NULL-customer and unrestricted false-only reconciliation conflict. No synthetic resolution chosen.',candidate_sum=str(total),all_false_source_total=str(expected['source_reconciliation_total']),nonnull_customer_all_dates_total=str(expected['nonnull_customer_total']))
        checks['undated_lifetime_population']=ck('gap','233 non-cancelled customers only have undated orders. Lifetime up-to-as_of membership and undated-only output inclusion undefined; distinct counts enforced separately.',undated_amount=str(expected['facts']['nonnull_undated_amount']))
    else:checks['values']=ck('pending','Rows unavailable')
    checks['workflow']=workflow or ck('pending','Trusted workflow unavailable');states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if any(s in states for s in ['pending','gap'])else 1
    return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
