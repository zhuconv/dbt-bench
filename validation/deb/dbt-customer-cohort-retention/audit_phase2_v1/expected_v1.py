"""Independent monthly cohorts from raw dated orders; exact rational rounding."""
from collections import defaultdict,Counter
from datetime import date,datetime
from decimal import Decimal as D
from fractions import Fraction
COLS={'cohort_retention':('cohort_month','months_since_first_order','cohort_size','retained_customers','retention_rate','cumulative_retained','cumulative_retention_rate'),'cohort_revenue':('cohort_month','months_since_first_order','cohort_size','total_revenue','revenue_per_customer','cumulative_revenue','cumulative_revenue_per_customer'),'cohort_summary':('cohort_month','cohort_size','total_lifetime_revenue','avg_revenue_per_customer','avg_orders_per_customer','months_active','retention_month_6','retention_month_12')}
def day(x):return x.date()if isinstance(x,datetime)else x
def month(x):return x.year*12+x.month-1
def text(i):return f'{i//12:04d}-{i%12+1:02d}'
def cents(x):
    f=Fraction(x)*100;q=f.numerator//f.denominator;r=f-q
    return [D(q)/100]if r<Fraction(1,2)else[D(q+1)/100]if r>Fraction(1,2)else[D(q)/100,D(q+1)/100]
def derive(raw):
    by=defaultdict(list);valid=[]
    for r in raw['orders']:
        if r['ordered_at']is not None and date(2023,1,1)<=day(r['ordered_at'])<=date(2024,12,31)and r['status']is not None and r['status'].strip()not in['CANCELLED','RETURNED']:
            assert r['customer_id']is not None,'NULL qualifying customer requires explicit policy';by[r['customer_id'].strip()].append(r);valid.append(r)
    members=defaultdict(set);activity=defaultdict(list)
    for cid,rs in by.items():
        cohort=min(month(r['ordered_at'])for r in rs);members[cohort].add(cid)
        for r in rs:activity[cohort,month(r['ordered_at'])-cohort].append((cid,r))
    outputs={k:[]for k in COLS};optional={k:[]for k in ['cohort_retention','cohort_revenue']};horizon=month(date(2024,12,1));differences=[]
    for cohort,ids in sorted(members.items()):
        size=len(ids);running=D(0);rounded_running={D(0)};allrows=[];active=[]
        for offset in range(horizon-cohort+1):
            rs=activity.get((cohort,offset),[]);allrows+=rs
            if rs:active.append(offset)
            amt=sum((D(r['grand_total'])for _,r in rs if r['grand_total']is not None),D(0));running+=amt;rounded_running={a+b for a in rounded_running for b in cents(amt)};assert len(rounded_running)<50
            common={'cohort_month':text(cohort),'months_since_first_order':offset,'cohort_size':size};ret={**common,'retained_customers':len({cid for cid,r in rs}),'retention_rate':cents(Fraction(len({cid for cid,r in rs})*100,size)),'cumulative_retained':size,'cumulative_retention_rate':[D(100)]};rev={**common,'total_revenue':cents(amt),'revenue_per_customer':cents(Fraction(amt)/size),'cumulative_revenue':cents(running),'cumulative_revenue_per_customer':cents(Fraction(running)/size)}
            if set(cents(running))!=rounded_running:differences.append({'cohort_month':text(cohort),'offset':offset,'round_raw_running':cents(running),'sum_rounded_months':sorted(rounded_running)})
            for name,row in [('cohort_retention',ret),('cohort_revenue',rev)]:
                (outputs if rs else optional)[name].append(row)
        total=sum((D(r['grand_total'])for _,r in allrows if r['grand_total']is not None),D(0))
        def retention(k):return None if cohort+k>horizon else cents(Fraction(len({cid for cid,r in activity.get((cohort,k),[])})*100,size))
        outputs['cohort_summary'].append({'cohort_month':text(cohort),'cohort_size':size,'total_lifetime_revenue':cents(total),'avg_revenue_per_customer':cents(Fraction(total)/size),'avg_orders_per_customer':cents(Fraction(len(allrows),size)),'months_active':len(active),'retention_month_6':retention(6),'retention_month_12':retention(12)})
    return {'outputs':outputs,'optional_zero_months':optional,'rounding_alternatives':differences,'facts':{'raw_orders':len(raw['orders']),'qualifying_orders':len(valid),'customers':len(by),'cohorts':len(members),'required_observed_months':len(outputs['cohort_retention']),'optional_no_activity_months':len(optional['cohort_retention']),'status_counts':dict(Counter(r['status']for r in valid)),'cumulative_rounding_policy_differences':len(differences),'scope':'Original qualifying orders and first valid calendar month. Observed buckets required; task does not explicitly demand sparse/dense zero months, so finite zero-month representation remains available. Raw sums retained until displayed rounding; actual sum-of-rounded-month alternative explicitly reported.'}}
