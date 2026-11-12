"""Independent exact-decimal interpretation of the three Weekly Sales outputs."""
from collections import Counter
from datetime import datetime,timedelta
from decimal import Decimal,ROUND_HALF_UP

STG=['order_id','customer_id','ordered_at','grand_total']
INTERMEDIATE=['week_start','week_number','order_count','unique_customers','total_revenue','avg_order_value']
MART=INTERMEDIATE+['prev_week_revenue','revenue_change','revenue_growth_pct','growth_status','rolling_4wk_avg_revenue','cumulative_revenue','is_best_week']
MODELS={'stg_orders__weekly':STG,'int_weekly_sales':INTERMEDIATE,'weekly_sales_growth':MART}
NUMBERS={'grand_total','week_number','order_count','unique_customers','total_revenue','avg_order_value','prev_week_revenue','revenue_change','revenue_growth_pct','rolling_4wk_avg_revenue','cumulative_revenue'}
MONEY={'total_revenue','avg_order_value','prev_week_revenue','revenue_change','rolling_4wk_avg_revenue','cumulative_revenue'}


def rounded(value):return value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
def trim(value):return value.strip(' ') if value is not None else None


def status(pct):
    if pct is None:return None
    if pct>=50:return 'Strong Growth'
    if pct>0:return 'Growing'
    if pct==0:return 'Stable'
    if pct>-50:return 'Declining'
    return 'Sharp Decline'


def derive(raw):
    staged=[];groups={}
    for row in raw:
        ts=datetime.fromisoformat(str(row['ordered_at'])) if row['ordered_at'] is not None else None
        state=trim(row['status'])
        if ts is None or not(datetime(2024,1,1)<=ts<datetime(2024,7,1)) or state is None or state in {'CANCELLED','RETURNED','FAILED'}:continue
        r={'order_id':trim(row['order_id']),'customer_id':trim(row['customer_id']),
           'ordered_at':str(ts),'grand_total':str(row['grand_total'])}
        staged.append(r)
        day=ts.date();week=day-timedelta(days=(day.weekday()+1)%7)
        groups.setdefault(str(week),[]).append(r)
    intermediate=[]
    for number,(week,rows) in enumerate(sorted(groups.items()),1):
        total=rounded(sum((Decimal(r['grand_total']) for r in rows),Decimal(0)))
        intermediate.append({'week_start':week,'week_number':number,'order_count':len(rows),
                             'unique_customers':len({r['customer_id'] for r in rows if r['customer_id'] is not None}),
                             'total_revenue':str(total),'avg_order_value':str(rounded(total/len(rows)))})
    mart=[];amounts=[];cumulative=Decimal(0)
    for i,row in enumerate(intermediate):
        amount=Decimal(row['total_revenue']);previous=amounts[-1] if amounts else None
        change=amount-previous if previous is not None else None
        pct=rounded(change/previous*100) if previous not in (None,Decimal(0)) else None
        amounts.append(amount);cumulative+=amount
        rolling=rounded(sum(amounts[-4:])/4) if len(amounts)>=4 else None
        mart.append({**row,'prev_week_revenue':str(previous) if previous is not None else None,
                     'revenue_change':str(change) if change is not None else None,
                     'revenue_growth_pct':str(pct) if pct is not None else None,'growth_status':status(pct),
                     'rolling_4wk_avg_revenue':str(rolling) if rolling is not None else None,
                     'cumulative_revenue':str(cumulative),'is_best_week':'Y' if amount==max(amounts) else 'N'})
    return {'stg_orders__weekly':sorted(staged,key=lambda r:r['order_id']),
            'int_weekly_sales':intermediate,'weekly_sales_growth':mart}


def compare(expected,snapshot):
    comparisons={}
    for model,columns in MODELS.items():
        observed=snapshot['models'][model];key='order_id' if model=='stg_orders__weekly' else 'week_start'
        keys=[str(r[key]) for r in observed['rows']];duplicates={k:n for k,n in Counter(keys).items() if n>1}
        wanted={str(r[key]):r for r in expected[model]};actual={str(r[key]):r for r in observed['rows']}
        missing=sorted(wanted.keys()-actual.keys());extra=sorted(actual.keys()-wanted.keys())
        missing_columns=sorted(set(columns)-set(observed['columns']));mismatches=[]
        for k in sorted(wanted.keys()&actual.keys()):
            for column in columns:
                if column in missing_columns:continue
                left,right=wanted[k][column],actual[k][column]
                if column in NUMBERS:
                    equal=(left is None and right is None) or (left is not None and right is not None and not isinstance(right,bool) and Decimal(str(left))==Decimal(str(right)))
                else:equal=(left is None and right is None) or (left is not None and right is not None and str(left)==str(right))
                if not equal:mismatches.append({'key':k,'column':column,'expected':left,'actual':right})
        comparisons[model]={'equal':not(missing_columns or missing or extra or duplicates or mismatches),
                            'missing_columns':missing_columns,'missing_keys':missing,'extra_keys':extra,'duplicate_keys':duplicates,
                            'mismatches':mismatches,'expected_rows':len(wanted),'actual_rows':len(keys)}
    return {'equal':all(c['equal'] for c in comparisons.values()),'models':comparisons}
