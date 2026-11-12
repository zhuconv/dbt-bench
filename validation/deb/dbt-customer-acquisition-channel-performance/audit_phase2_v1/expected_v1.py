"""Direct declared-input joins, distinct-order aggregation, and NULL-date alternatives."""
from decimal import Decimal as D
from fractions import Fraction as F
from collections import defaultdict,Counter
COLS=['acquisition_channel','customer_count','total_orders','total_revenue','average_order_value','customer_lifetime_value','average_orders_per_customer','repeat_purchase_rate','channel_tier']
def channel(a,s,m):
 if a is not None:return a
 if s=='google'and m=='cpc':return'PAID_SEARCH'
 if s=='google'and m=='organic':return'ORGANIC_SEARCH'
 if s in['facebook','instagram']:return'SOCIAL_MEDIA'
 if s=='email'or m=='email':return'EMAIL'
 if s=='direct'or(s is None and m is None):return'DIRECT'
 return'OTHER'
def tier(cltv,rate,count):return'HIGH_VALUE'if cltv>500 and rate>30 else'VOLUME'if count>100 and cltv>200 else'LOW_QUALITY'if cltv<100 or rate<10 else'STANDARD'
def derive(raw):
 index=defaultdict(list)
 for s in raw['orders']:index[s['order_id']].append(s)
 customers=defaultdict(list);exclusions=Counter();fallback=[]
 for o in raw['enriched_orders']:
  if o['status']is None or o['status']=='CANCELLED':exclusions['status']+=1;continue
  if o['customer_id']is None:exclusions['null_customer']+=1;continue
  if not index.get(o['order_id']):exclusions['no_staging_match']+=1;continue
  for s in index[o['order_id']]:
   customers[o['customer_id']].append(o|{'channel':channel(s['attribution_channel'],s['utm_source'],s['utm_medium'])})
   if s['attribution_channel']is None:fallback.append(s)
 firsts={};undated=[];ties=[];affected=[]
 for cid,rs in customers.items():
  dated=[r for r in rs if r['ordered_at']is not None]
  if not dated:undated.append(cid);continue
  dt=min(r['ordered_at']for r in dated);chs={r['channel']for r in dated if r['ordered_at']==dt}
  if len(chs)!=1:ties.append({'customer_id':cid,'channels':sorted(chs)});continue
  firsts[cid]=next(iter(chs))
  if len(dated)<len(rs):affected.append(cid)
 policies={}
 for label,keep_null in [('exclude_all_null_dates',False),('first_dated_channel_include_all_joined_orders',True)]:
  groups=defaultdict(list)
  for cid,ch in firsts.items():groups[ch].append((cid,[r for r in customers[cid]if keep_null or r['ordered_at']is not None]))
  rows=[];details=[]
  for ch,cs in sorted(groups.items()):
   rs=[r for _,rr in cs for r in rr];n=len(cs);orders=len({r['order_id']for r in rs if r['order_id']is not None});money=sum((F(r['grand_total'])for r in rs if r['grand_total']is not None),F(0));repeat=sum(len({r['order_id']for r in rr if r['order_id']is not None})>=2 for _,rr in cs);aov=money/orders;cltv=money/n;opc=F(orders,n);rate=F(repeat*100,n);rows.append([ch,n,orders,str(money),str(aov),str(cltv),str(opc),str(rate),tier(cltv,rate,n)]);details.append({'channel':ch,'customers':n,'joined_rows':len(rs),'distinct_orders':orders,'repeat_customers':repeat,'exact_revenue':str(money),'customer_ids':[cid for cid,_ in cs]})
  policies[label]={'rows':rows,'groups':details}
 return {'columns':COLS,'policies':policies,'profile':{'raw_enriched_rows':len(raw['enriched_orders']),'raw_staging_rows':len(raw['orders']),'excluded':dict(exclusions),'eligible_customer_groups':len(customers),'dated_first_customers':len(firsts),'undated_only_customer_ids':sorted(undated),'known_dated_customers_with_undated_orders':sorted(affected),'earliest_different_channel_ties':ties,'fallback_joined_rows':len(fallback)},'scope':'Exact bagjoin and distinct-order arithmetic over declared taskinputs; two executed NULL-date interpretations, not uniquely lawful policy. No model/gold/test invocation.'}
