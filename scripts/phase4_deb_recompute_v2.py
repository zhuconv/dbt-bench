"""Independent fixed-input Python arithmetic for four phase4 DEB reviews.

No dbt, gold, reference SQL, or native checker is called by this module.
Underspecified choices are explicit review policies, not inferred from reward.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import math

from audit_tables import TablePolicy, audit_table
from dbt_bench.comparison import Column, TableContract

D = Decimal
INTS = ('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMS = INTS + ('FLOAT','REAL','DOUBLE')

def dec(x): return D(str(x))
def day(x): return x.date() if type(x) is datetime else x
def month(x): return x.year * 12 + x.month - 1
def monthstr(n): return f'{n // 12:04d}-{n % 12 + 1:02d}'

@dataclass(frozen=True)
class Rounded:
    value: Decimal
    digits: int = 2

def nearest(x, digits=2):
    """Either adjacent result only at an exact mathematical halfway tie."""
    x = dec(x); unit = D(10) ** -digits
    a, b = x.quantize(unit, rounding=ROUND_FLOOR), x.quantize(unit, rounding=ROUND_CEILING)
    return {a} if x-a < b-x else {b} if b-x < x-a else {a,b}

def numbers_equal(actual, expected):
    if actual is None or expected is None: return actual is expected
    if type(actual) not in (int, float, D) or type(actual) is bool: return False
    if not math.isfinite(actual): return False
    if isinstance(expected, Rounded): allowed = nearest(expected.value, expected.digits)
    else: allowed = {dec(expected)}
    # Float representation error is separate from business rounding tolerance.
    tol = max(D(str(math.ulp(actual))) * 8, D('1e-12')) if type(actual) is float else D(0)
    return any(abs(dec(actual)-x) <= tol for x in allowed)

def value_equal(actual, expected):
    if expected is None: return actual is None
    if type(expected) in (str, int, bool, date): return type(actual) is type(expected) and actual == expected
    return numbers_equal(actual, expected)

def ntile(values, buckets=5):
    ordered=sorted(values); q,r=divmod(len(ordered),buckets); out={}; pos=0
    for tile in range(buckets):
        for _ in range(q+(tile<r)):
            out[ordered[pos][1]]=tile+1; pos+=1
    return out

def percentiles(values):
    ordered=sorted(values); first={}
    for i,(value,key) in enumerate(ordered): first.setdefault(value,i)
    return {key:Rounded(D(first[value])*100/(len(ordered)-1) if len(ordered)>1 else D(0)) for value,key in ordered}

def warning(raw, actual=None):
    original=raw['enriched']; valid=[r for r in original if r['customer_id'] is not None and r['ordered_at'] is not None]
    if not valid: raise ValueError('No dated non-NULL customer input: no as-of baseline')
    asof=max(r['ordered_at'] for r in valid); week=day(asof)-timedelta(days=asof.weekday()); groups=defaultdict(list)
    for row in valid: groups[row['customer_id']].append(row)
    output=[]
    for cid,rows in groups.items():
        first=min(day(r['ordered_at']) for r in rows); last=max(day(r['ordered_at']) for r in rows); age=(day(asof)-last).days
        output.append(dict(customer_id=cid,week_start=week,first_order_date=first,last_order_date=last,
            days_since_last_order=age,order_count_90d=len({r['order_id'] for r in rows if r['order_id'] is not None and r['ordered_at']>=asof-timedelta(days=90)}),
            lifetime_orders=len({r['order_id'] for r in rows if r['order_id'] is not None}),
            lifetime_revenue=sum((dec(r['grand_total']) for r in rows if r['grand_total'] is not None),D(0)),
            churn_risk_tier='low' if age<=30 else 'medium' if age<=90 else 'high'))
    return {'rpt_customer_churn_early_warning_fixed':output},{'raw_rows':len(original),'valid_rows':len(valid),'customers':len(groups),'null_customer_rows':sum(r['customer_id'] is None for r in original),'null_date_rows':sum(r['ordered_at'] is None for r in original),'asof':asof,'week':week}

def cohorts(raw, actual=None):
    mapping={r['customer_key']:r['customer_id'] for r in raw['dim_customer']}
    dates={r['date_key']:day(r['full_date']) for r in raw['dim_date']}
    masters={r['customer_id']:r for r in raw['customers']}
    if len(mapping)!=len(raw['dim_customer']) or len(dates)!=len(raw['dim_date']) or len(masters)!=len(raw['customers']): raise ValueError('Input join keys not unique')
    groups=defaultdict(list); excluded=Counter()
    for r in raw['sales']:
        cid=mapping.get(r['customer_key']); dt=dates.get(r['date_key'])
        if cid is None or dt is None: excluded['missing_customer_or_date']+=1; continue
        groups[cid].append(dict(r,dt=dt))
    cohort_rows=[]; clv=[]; membership=defaultdict(set); activity=defaultdict(set); money=defaultdict(lambda:D(0)); maxperiod={}
    provided={r['customer_id']:r for r in (actual or {}).get('customer_clv',[]) if 'customer_id' in r}
    for cid,rs in groups.items():
        first=min(r['dt'] for r in rs); last=max(r['dt'] for r in rs); cm=month(first); cmtext=monthstr(cm); earliest=[r for r in rs if r['dt']==first]
        # Current data has no earliest-date ties; fail closed instead of inventing a tie rule.
        if len(earliest)!=1: raise ValueError('Earliest transaction ties require an explicit legal-alternative policy')
        master=masters.get(cid,{})
        if not master: raise ValueError('Unmapped customer demographics require review')
        signup=day(master['acquisition_date']); channel={'NORTH':'Online','SOUTH':'Retail','EAST':'Partner','WEST':'Direct'}.get(master['legacy_region_code'],'Unknown')
        cohort_rows.append(dict(customer_id=cid,cohort_month=cmtext,first_transaction_date=first,
            first_transaction_amount=dec(earliest[0]['total_amount']),signup_to_first_purchase_days=(first-signup).days if signup else None,acquisition_channel=channel))
        membership[cm].add(cid); maxperiod[cm]=max(maxperiod.get(cm,0),month(last)-cm)
        for r in rs:
            period=month(r['dt'])-cm; activity[(cm,period)].add(cid); money[(cm,period)]+=dec(r['total_amount'])
        total=sum((dec(r['total_amount']) for r in rs),D(0)); n=len(rs); life=max(1,month(last)-cm+1); since=month(date(2024,12,31))-month(last)
        f=D('.7') if n>=10 else D('.85') if n>=5 else D(1) if n>=2 else D('1.3')
        a=D('.5') if since<=1 else D('.8') if since<=3 else D(1) if since<=6 else D('1.2')
        churn=min(D(1),D(since)/12*f*a); rate=total/life
        # Downstream uses validated displayed rounding choices; this preserves
        # lawful exact-halfway alternatives without accepting arbitrary cents.
        candidate=provided.get(cid,{})
        actual_rate=candidate.get('monthly_revenue_rate'); actual_churn=candidate.get('churn_probability')
        chosen_rate=dec(actual_rate) if numbers_equal(actual_rate,Rounded(rate)) else min(nearest(rate))
        chosen_churn=dec(actual_churn) if numbers_equal(actual_churn,Rounded(churn)) else min(nearest(churn))
        prediction=chosen_rate*12*(1-chosen_churn)
        actual_prediction=candidate.get('predicted_clv_12m')
        chosen_prediction=dec(actual_prediction) if numbers_equal(actual_prediction,Rounded(prediction)) else min(nearest(prediction))
        tier='Platinum' if chosen_prediction>=1000 else 'Gold' if chosen_prediction>=500 else 'Silver' if chosen_prediction>=100 else 'Bronze'
        clv.append(dict(customer_id=cid,cohort_month=cmtext,total_transactions=n,total_revenue=total,
            avg_transaction_value=Rounded(total/n),customer_lifespan_months=life,monthly_revenue_rate=Rounded(rate),
            months_since_last_transaction=since,churn_probability=Rounded(churn),predicted_clv_12m=Rounded(prediction),customer_tier=tier))
    retention=[]
    for cm,people in membership.items():
        cumulative=D(0)
        for p in range(maxperiod[cm]+1):
            active=activity[(cm,p)]; cumulative+=money[(cm,p)]
            retention.append(dict(cohort_month=monthstr(cm),period_number=p,period_month=monthstr(cm+p),cohort_size=len(people),active_customers=len(active),
                retained_customers=len(active if p==0 else active & activity[(cm,p-1)]),retention_rate=Rounded(D(len(active))/len(people),4),
                period_revenue=money[(cm,p)],cumulative_revenue=cumulative))
    return {'customer_cohorts':cohort_rows,'cohort_retention':retention,'customer_clv':clv},{'raw_sales':len(raw['sales']),'included_sales':sum(map(len,groups.values())),'customers':len(groups),'cohort_periods':len(retention),'empty_periods':sum(r['active_customers']==0 for r in retention),'excluded':dict(excluded),'reviewed_scope':'All historical dimension mappings joined to existing dates; transaction grain SALE_KEY; signed signup lag; exact halfway alternatives and correlated displayed-rounding downstream choices.'}

def rfm2(raw, actual=None):
    rows=[r for r in raw['orders'] if r['status'] is not None and r['status'] not in ('CANCELLED','RETURNED')]
    if any(r['customer_id'] is None or r['ordered_at'] is None or r['grand_total'] is None for r in rows): raise ValueError('Input NULL policy requires review')
    groups=defaultdict(list)
    for r in rows: groups[r['customer_id']].append(r)
    metrics={}; activity=defaultdict(list); members=defaultdict(set)
    for cid,rs in groups.items():
        ds=sorted(day(r['ordered_at']) for r in rs); first,last=ds[0],ds[-1]; total=sum((dec(r['grand_total']) for r in rs),D(0)); n=len(rs)
        q={i:[r for r in rs if day(r['ordered_at']).year==2024 and (day(r['ordered_at']).month-1)//3+1==i] for i in range(1,5)}
        nq={i:len(q[i]) for i in q}; q3=sum((dec(r['grand_total']) for r in q[3]),D(0)); q4=sum((dec(r['grand_total']) for r in q[4]),D(0))
        trend='accelerating' if nq[4]>nq[3]>=nq[2] else 'decelerating' if nq[4]<nq[3]<=nq[2] else 'churning' if nq[4]==0 and nq[3]>0 else 'stable'
        metrics[cid]=dict(customer_id=cid,first_order_date=first,last_order_date=last,customer_tenure_days=(date(2025,1,1)-first).days,
            recency_days=(date(2025,1,1)-last).days,total_orders=n,total_spent=total,avg_order_value=Rounded(total/n),
            avg_days_between_orders=Rounded(D((last-first).days)/(n-1)) if n>1 else Rounded(D(0)),
            **{f'orders_q{i}_2024':nq[i] for i in range(1,5)},quarterly_trend=trend,spending_velocity=Rounded((q4-q3)*100/q3) if q3 else None)
        if first.year==2024:
            cm=month(first); members[cm].add(cid)
            for r in rs:
                dt=day(r['ordered_at'])
                if dt<date(2025,1,1): activity[(cm,month(dt)-cm)].append(r)
    for metric,score,percentile,sign in [('recency_days','recency_score','recency_percentile',-1),('total_orders','frequency_score','frequency_percentile',1),('total_spent','monetary_score','monetary_percentile',1)]:
        pairs=[(sign*r[metric],cid) for cid,r in metrics.items()]; tiles=ntile(pairs); perc=percentiles(pairs)
        for cid,r in metrics.items():r[score]=tiles[cid];r[percentile]=perc[cid]
    provided={r['customer_id']:r for r in (actual or {}).get('rfm_segments',[]) if 'customer_id' in r}; selected_ltv={}
    for cid,r in metrics.items():
        total=r['recency_score']+r['frequency_score']+r['monetary_score']; avg=D(total)/3
        segment='Champions' if avg>=D('4.5') else 'Loyal' if avg>=D('3.5') else 'Potential' if avg>=D('2.5') else 'At Risk' if avg>=D('1.5') else 'Lost'
        churn=max(0,min(100,(5-r['recency_score'])*15+(5-r['frequency_score'])*10+(5-r['monetary_score'])*5+{'accelerating':-10,'decelerating':15,'churning':30,'stable':0}[r['quarterly_trend']]))
        tenure=r['customer_tenure_days']; months={'Champions':36,'Loyal':24,'Potential':12,'At Risk':6,'Lost':0}[segment]
        ltv=max(D(0),r['total_spent']*30/tenure*months*(1-D(churn)/100)) if tenure>0 else D(0)
        r.update(rfm_score=total,rfm_segment=segment,churn_probability=churn,lifetime_value_estimate=Rounded(ltv))
        candidate=provided.get(cid,{}).get('lifetime_value_estimate');selected_ltv[cid]=dec(candidate) if numbers_equal(candidate,Rounded(ltv)) else min(nearest(ltv))
    bysegment=defaultdict(list)
    for r in metrics.values():bysegment[r['rfm_segment']].append(r)
    summary=[]
    for segment,rs in bysegment.items():
        total=sum((r['total_spent'] for r in rs),D(0)); ltv=sum((selected_ltv[r['customer_id']] for r in rs),D(0));n=len(rs)
        summary.append(dict(rfm_segment=segment,customer_count=n,pct_of_total=Rounded(D(n)*100/len(metrics)),total_revenue=total,
            avg_revenue_per_customer=total/n,avg_churn_probability=D(sum(r['churn_probability'] for r in rs))/n,
            total_lifetime_value=ltv,avg_lifetime_value=ltv/n,churning_customers=sum(r['quarterly_trend']=='churning' for r in rs),accelerating_customers=sum(r['quarterly_trend']=='accelerating' for r in rs)))
    actions={'Champions':('Executive outreach with exclusive preview access','VIP early access and personalized recommendations'),
        'Loyal':('Personal account manager contact with special offer','Loyalty program upgrade with bonus points'),
        'Potential':('Targeted email series with progressive discounts',)*2,'At Risk':('Urgent win-back: 30% off next purchase within 7 days',)*2,'Lost':('Final reactivation: 50% off or account closure notice',)*2}
    recommendations=[]
    for cid,r in metrics.items():
        churn=r['churn_probability'];trend=r['quarterly_trend'];segment=r['rfm_segment']
        risk='CRITICAL' if churn>=80 or trend=='churning' and segment in ('Loyal','Champions') else 'HIGH' if churn>=60 or trend=='churning' else 'MEDIUM' if churn>=40 or trend=='decelerating' else 'LOW'
        recommendations.append(dict(customer_id=cid,rfm_segment=segment,rfm_score=r['rfm_score'],quarterly_trend=trend,churn_probability=churn,risk_level=risk,
            intervention_type={'CRITICAL':'immediate_outreach','HIGH':'win_back_campaign','MEDIUM':'engagement_program','LOW':'loyalty_program'}[risk],
            recommended_action=actions[segment][0 if risk in ('CRITICAL','HIGH') else 1],estimated_value=selected_ltv[cid],urgency_days={'CRITICAL':3,'HIGH':7,'MEDIUM':14,'LOW':30}[risk],
            priority_score=churn*2+r['rfm_score']*5+{'CRITICAL':100,'HIGH':50,'MEDIUM':25,'LOW':0}[risk]+{'Champions':50,'Loyal':40,'Potential':30,'At Risk':20,'Lost':10}[segment]))
    cohort=[]
    for (cm,p),rs in activity.items():
        count=len({r['customer_id'] for r in rs});total=sum((dec(r['grand_total']) for r in rs),D(0))
        cohort.append(dict(cohort_month=monthstr(cm),months_since_first=p,cohort_size=len(members[cm]),retained_customers=count,
            retention_rate=Rounded(D(count)*100/len(members[cm])),cohort_revenue=total,avg_order_value=total/len(rs)))
    return {'rfm_segments':list(metrics.values()),'rpt_segment_summary':summary,'rpt_customer_recommendations':recommendations,'rfm_cohort_retention':cohort}, {'valid_orders':len(rows),'customers':len(groups),'cohort_pairs':len(cohort),'reviewed_scope':'All valid history including future orders; metric-only percentile ties; listed trend precedence; 30-day monthly rate, nonpositive-tenure LTV zero; active cohort pairs only. These choices are disclosed by independent blind author and native accepts, not uniquely entailed for all unseen data.'}

KEYS={'cohort_retention':('cohort_month','period_number'),'rfm_cohort_retention':('cohort_month','months_since_first'),'rpt_segment_summary':('rfm_segment',),'channel_revenue_attribution':('channel',)}

def compare_outputs(actual, expected):
    results={}; all_errors=[]
    for name,rows in expected.items():
        candidate=actual.get(name,{});table=candidate.get('table')
        if table is None:
            results[name]={'reward':0,'errors':[{'rule':'required_table','reason':candidate.get('capture_error','absent')}], 'checked_values':0};all_errors.extend(results[name]['errors']);continue
        if not rows: raise ValueError('Empty expected table requires a declared column policy')
        fields=tuple(rows[0]); keys=KEYS.get(name,('customer_id',));columns=[];sqltypes={}
        for f in fields:
            examples=[r[f] for r in rows if r[f] is not None];sample=examples[0] if examples else None
            kind='integer' if type(sample)is int else 'text' if type(sample)is str else 'boolean' if type(sample)is bool else 'exact' if type(sample)is date else 'numeric'
            columns.append(Column(f,kind)); sqltypes[f]=INTS if kind=='integer' else ('VARCHAR',) if kind=='text' else ('BOOLEAN',) if kind=='boolean' else ('DATE',) if kind=='exact' else NUMS+(table.physical_sql_types.get(f),) if str(table.physical_sql_types.get(f,'')).startswith('DECIMAL(') else NUMS
        interface=audit_table(table,TablePolicy(TableContract(tuple(columns),keys=keys,allow_extra_columns=True),sqltypes,not_null=keys))
        errors=[];checked=0
        if interface['reward']!=1:errors.append({'rule':'interface','detail':interface})
        if all(f in table.columns for f in fields):
            want={tuple(r[k] for k in keys):r for r in rows};got={}
            for r in table.rows:got.setdefault(tuple(r[k] for k in keys),[]).append(r)
            for k in want.keys()-got.keys():errors.append({'rule':'missing_row','key':k})
            for k in got.keys()-want.keys():errors.append({'rule':'extra_row','key':k})
            for k in want.keys()&got.keys():
                if len(got[k])!=1:continue
                for f in fields:
                    checked+=1
                    if not value_equal(got[k][0][f],want[k][f]):errors.append({'rule':'value','key':k,'field':f,'actual':got[k][0][f],'expected':want[k][f]})
        results[name]={'reward':0 if errors else 1,'interface':interface,'checked_values':checked,'errors':errors}
        all_errors.extend(dict(table=name,**x) for x in errors)
    return {'reward':0 if all_errors else 1,'status':'scored','tables':results,'error_count':len(all_errors),'errors':all_errors}

def tiering_channel_proof(raw, actual):
    """Finite counterexample only. Does not certify remaining tiering formulas."""
    channels={r['channel_key']:r for r in raw['channels']}; dates={r['date_key']:day(r['full_date']) for r in raw['dates']}; mapping={r['customer_key']:r['customer_id'] for r in raw['dim_customer']}
    totals=defaultdict(lambda:[D(0),D(0),D(0),set(),0]); types=Counter()
    for r in raw['sales']:
        c=channels.get(r['channel_key']); dt=dates.get(r['date_key']);cid=mapping.get(r['customer_key'])
        if not c or not dt or cid is None or r['total_amount'] is None or dec(r['total_amount'])<=0:continue
        name=c['channel_name']; kind=c['channel_type']; x=dec(r['total_amount']); weight={'Online':D('1.2'),'Store':D(1),'Partner':D('.8')}.get(kind,D(1)); cost={'Online':D('.15'),'Store':D('.25'),'Partner':D('.35')}.get(kind,D('.20'))
        totals[name][0]+=x;totals[name][1]+=x*weight;totals[name][2]+=x*cost;totals[name][3].add(kind);totals[name][4]+=1;types[kind]+=1
    details=[]
    for row in actual['channel_revenue_attribution']['rows']:
        base=totals.get(row['channel'])
        if base:
            margin=(base[1]-base[2])/base[1]*100
            details.append({'channel':row['channel'],'raw_channel_types':sorted(base[3]),'all_dated_raw_revenue':base[0],'raw_revenue_matches':numbers_equal(row['raw_revenue'],Rounded(base[0])),
                'independent_weighted_revenue':Rounded(base[1]),'actual_weighted_revenue':row['weighted_revenue'],'weighting_pass':numbers_equal(row['weighted_revenue'],Rounded(base[1])),
                'independent_margin':Rounded(margin),'actual_margin':row['channel_contribution_margin'],'margin_pass':numbers_equal(row['channel_contribution_margin'],Rounded(margin))})
    definitive=[r for r in details if r['raw_revenue_matches'] and (not r['weighting_pass'] or not r['margin_pass'])]
    return {'reward':0 if definitive else None,'status':'scored' if definitive else 'unknown','counterexamples':definitive,'channel_details':details,'source_channel_type_counts':dict(types),
        'scope':'Literal instruction CASE weights/default costs, conditioned on independently matching submitted channel raw revenue. No claim of whole tiering audit qualification or complete legal reference.'}
