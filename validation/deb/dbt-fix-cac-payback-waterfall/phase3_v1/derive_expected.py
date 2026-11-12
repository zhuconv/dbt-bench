"""Independent Python campaign/day aggregation from the declared spend source."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

def nearest(value,places):
 scale=10**places;x=Fraction(value)*scale;q,r=divmod(x.numerator,x.denominator)
 return tuple(Decimal(v)/scale for v in([q,q+1]if r*2==x.denominator else[q+(r*2>x.denominator)]))
def derive(inputs,decode):
 table=inputs['relations']['main.stg_fact_marketing_spend'];names=[c['name'].lower()for c in table['schema']];rows=[dict(zip(names,map(decode,r)))for r in table['rows']];days=defaultdict(lambda:[Decimal(0),Decimal(0)]);channels=defaultdict(lambda:defaultdict(Decimal));source_totals=defaultdict(lambda:[Decimal(0),Decimal(0)])
 for r in rows:
  assert all(v is not None for v in r.values()),'Current source NULL requires separate policy review';assert type(r['channel_key'])is int;day=datetime.strptime(str(r['date_key']),'%Y%m%d').date();spend,revenue=r['spend_amount'],r['revenue_attributed'];assert spend>=0 and revenue>=0;assert spend*100==int(spend*100)and revenue*100==int(revenue*100),'Original source has sub-cent money; staging rounding needs investigation';key=(r['campaign_id'],day);days[key][0]+=spend;days[key][1]+=revenue;channels[r['campaign_id']][r['channel_key']]+=spend;source_totals[r['campaign_id']][0]+=spend;source_totals[r['campaign_id']][1]+=revenue
 allowed={campaign:sorted(ch for ch,total in sums.items()if total==max(sums.values()))for campaign,sums in channels.items()};result=[];ratios={};minimum=None
 for campaign in sorted(channels):
  dated=sorted((day,sums)for(c,day),sums in days.items()if c==campaign);cohort=dated[0][0];cs=cr=Decimal(0)
  for day,(spend,revenue)in dated:
   cs+=spend;cr+=revenue;assert cs>0,'Current zero denominator lacks a specified payback ratio branch';minimum=cs if minimum is None else min(minimum,cs);offset=(day-cohort).days;options=nearest(Fraction(cr)/Fraction(cs),4);ratios[campaign,offset]=options;result.append({'campaign_id':campaign,'channel_key':allowed[campaign][0],'cohort_date':cohort,'days_since_cohort':offset,'daily_spend':nearest(spend,2)[-1],'daily_revenue_attributed':nearest(revenue,2)[-1],'cumulative_spend':nearest(cs,2)[-1],'cumulative_revenue':nearest(cr,2)[-1],'payback_ratio':options[-1],'is_paid_back':cr>=cs})
 return result,{'rows':len(rows),'campaigns':len(channels),'allowed_channels':allowed,'source_totals':dict(source_totals),'ratio_options':ratios,'minimum_cumulative_spend':minimum,'channel_ties':sum(len(v)>1 for v in allowed.values())}
