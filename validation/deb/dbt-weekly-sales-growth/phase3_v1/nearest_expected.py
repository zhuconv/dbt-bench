"""Exact nearest-cent arithmetic; only true half points admit both endpoints."""
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
D=Decimal

def nearest(value):
 scaled=Fraction(value)*100;lo=scaled.numerator//scaled.denominator;remainder=scaled-lo
 values=[lo]if remainder<Fraction(1,2)else[lo+1]if remainder>Fraction(1,2)else[lo,lo+1]
 return tuple(D(v)/100 for v in values)
def numeric(v):return type(v)in(int,float,D)and D(str(v)).is_finite()
def choice(value,expected):
 allowed=nearest(expected)
 return D(str(value))if numeric(value)and D(str(value))in allowed else allowed[-1]
def conditioned(model,rows,baseline):
 target=[dict(r)for r in baseline[model]]
 if model=='stg_orders__weekly':return target,{}
 sums={}
 for r in baseline['stg_orders__weekly']:
  day=r['ordered_at'].date();week=day-timedelta(days=(day.weekday()+1)%7);sums[week]=sums.get(week,D(0))+r['grand_total']
 lookup={r.get('week_start'):r for r in rows};admitted={};proof={}
 for r in target:
  week=r['week_start'];actual=lookup.get(week,{});allowed=nearest(sums[week]);admitted[week]=numeric(actual.get('total_revenue'))and D(str(actual['total_revenue']))in allowed;r['total_revenue']=choice(actual.get('total_revenue'),sums[week]);r['avg_order_value']=choice(actual.get('avg_order_value'),Fraction(r['total_revenue'])/r['order_count']);proof[str(week)]={'raw_sum':str(sums[week]),'total_nearest_options':[str(v)for v in allowed],'reported_total_admissible':admitted[week]}
 if model=='int_weekly_sales':return target,proof
 amounts=[]
 for index,r in enumerate(target):
  week=r['week_start'];actual=lookup.get(week,{});current=r['total_revenue'];previous=amounts[-1]if amounts else None;change=current-previous if previous is not None else None;percent=Fraction(change)/Fraction(previous)*100 if previous not in(None,D(0))else None;r['prev_week_revenue']=previous;r['revenue_change']=change;r['revenue_growth_pct']=choice(actual.get('revenue_growth_pct'),percent)if percent is not None else None;pct=r['revenue_growth_pct'];r['growth_status']=None if pct is None else'Strong Growth'if pct>=50 else'Growing'if pct>0 else'Stable'if pct==0 else'Declining'if pct>-50 else'Sharp Decline';amounts.append(current);r['rolling_4wk_avg_revenue']=choice(actual.get('rolling_4wk_avg_revenue'),Fraction(sum(amounts[-4:]))/4)if len(amounts)>=4 else None;r['cumulative_revenue']=sum(amounts);r['is_best_week']='Y'if current==max(amounts)else'N'
 return target,proof
