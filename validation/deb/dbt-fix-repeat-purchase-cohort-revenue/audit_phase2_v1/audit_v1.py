"""Repeat-purchase keyed output audit from full per-order MIN/MIN/SUM."""
from collections import defaultdict
from datetime import date
from decimal import Decimal as D
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import COLS
NAME='rpt_repeat_purchase_cohort_revenue_fixed';COUNTS={'months_since_cohort','cohort_size','active_customers','orders'}
def ck(status,reason,**kw):return dict(status=status,reason=reason,**kw)
def finite(x):return type(x)in(int,float,D)and D(str(x)).is_finite()
def key(r):return r.get('cohort_month'),r.get('months_since_cohort')
def evaluate(outputs,expected,workflow=None):
    checks={};o=outputs.get(NAME,{});rows=o.get('rows');cols=COLS[NAME];policy=TablePolicy(TableContract(tuple(Column(c,'date'if c=='cohort_month'else'integer'if c in COUNTS else'numeric')for c in cols),keys=('cohort_month','months_since_cohort'),allow_extra_columns=True),{c:('DATE',)if c=='cohort_month'else('INTEGER',)if c in COUNTS else('DECIMAL(18,2)',)for c in cols},not_null=cols,relation_types=('TABLE','BASE TABLE'))
    if o.get('exists')is False:checks['presence']=ck('fail','Required relation actually absent')
    if o.get('table')is not None:
        result=audit_table(o['table'],policy);checks['interface']=ck('pending'if result['status']=='unknown'else'pass'if result['reward']==1 else'fail','Required physical types, TABLE and two-column grain; row/column order is not constrained',details=result)
    else:checks['interface']=ck('pending','Exact interface not captured')
    if o.get('relation_type')is not None:checks['table']=ck('pass'if o['relation_type']in['TABLE','BASE TABLE']else'fail','Actually captured relation type')
    if rows is not None:
        want={key(r):r for r in expected['outputs'][NAME]};got={key(r):r for r in rows};checks['population']=ck('pass'if len(got)==len(rows)and set(want)==set(got)else'fail','Exact source-derived cohort/bucket population after per-order MIN/MIN/SUM then ignoring wholly undated orders',missing=[str(k)for k in set(want)-set(got)],extra=[str(k)for k in set(got)-set(want)])
        errors=defaultdict(list)
        for k,r in got.items():
            if k not in want:continue
            for c,e in want[k].items():
                a=r.get(c);ok=type(a)is type(e)and a==e if c=='cohort_month'or c in COUNTS else finite(a)and D(str(a))==D(e)
                if not ok:errors[c].append({'key':str(k),'actual':str(a),'expected':str(e)})
        for c in cols:
            if c in ['cohort_month','months_since_cohort']:continue
            checks[c]=ck('fail'if errors[c]else'pass','Independent full per-order source arithmetic for '+c,error_count=len(errors[c]),examples=errors[c][:15])
        bad=[];running=defaultdict(lambda:D(0));cumerrors=[]
        for r in sorted(rows,key=lambda r:(str(r.get('cohort_month')),r.get('months_since_cohort')if type(r.get('months_since_cohort'))is int else -1)):
            period=r.get('cohort_month');offset=r.get('months_since_cohort');money=r.get('revenue');cum=r.get('cumulative_revenue');active=r.get('active_customers');size=r.get('cohort_size');ok=type(period)is date and period.day==1 and type(offset)is int and offset>=0 and type(active)is int and type(size)is int and 0<=active<=size and finite(money)and finite(cum)and 0<=money<=cum
            if not ok:bad.append(str(key(r)));continue
            running[period]+=D(str(money))
            if abs(D(str(cum))-running[period])>D('.01'):cumerrors.append(str(key(r)))
        checks['quality']=ck('fail'if bad else'pass','Valid first-of-month/nonnegative period, active<=size, nonnegative revenue and cumulative>=current',examples=bad[:12]);checks['reported_running_sum']=ck('fail'if cumerrors else'pass','Reported cumulative matches exact prior-or-equal reported bucket sum within explicit0.01',examples=cumerrors[:12])
        total_orders=sum(r['orders']for r in rows if type(r.get('orders'))is int);total_money=sum((D(str(r['revenue']))for r in rows if finite(r.get('revenue'))),D(0));source=expected['correct'];checks['source_reconciliation']=ck('pass'if abs(total_orders-source['source_order_total'])<=1 and abs(total_money-source['source_revenue_total'])<=abs(source['source_revenue_total'])*D('.005')else'fail','Global valid deduplicated-order totals, explicit +/-1order and0.5%revenue; entirely undated orders ignored after aggregation',actual_orders=total_orders,expected_orders=source['source_order_total'],actual_revenue=str(total_money),expected_revenue=str(source['source_revenue_total']))
    else:checks['values']=ck('pending','Rows unavailable')
    checks['workflow']=workflow or ck('pending','Trusted source-build/order/model-preservation evidence unavailable');states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if any(s in states for s in ['pending','gap'])else 1
    return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
