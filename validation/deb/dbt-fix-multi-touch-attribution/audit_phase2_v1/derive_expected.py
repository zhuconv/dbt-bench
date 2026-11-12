"""Original-input single-session attribution proof, from raw rows rather than SQL/gold.

The original data has one session per effective user, no purchase events and no
joined revenue column. All five mathematical models then have identical credits.
Other input shapes require a separately reviewed rounding/tie policy, not guesses.
"""
from collections import defaultdict,Counter
from datetime import datetime,date
from decimal import Decimal
from fractions import Fraction as F
MODELS=('last_touch','first_touch','linear','time_decay','position_based')
KEY=('attribution_date','channel','medium','campaign','attribution_model')
def rows(inputs,table,decode):
 t=inputs['relations'][table];cols=[c['name'].lower()for c in t['schema']];return [dict(zip(cols,map(decode,r)))for r in t['rows']]
def nearest(x,places):
 factor=10**places;n=x.numerator*factor;d=x.denominator;q,r=divmod(n,d);ints=[q]if 2*r<d else[q+1]if 2*r>d else[q,q+1];return [Decimal(v).scaleb(-places)for v in ints]
def derive(inputs,decode):
 original=rows(inputs,'main.stg_ga__sessions',decode);events=rows(inputs,'main.stg_ga__events',decode);joined=inputs['metadata']['optional_joined'];assert joined['exists']and not joined['has_t2_event_value'],'This fixed-input proof does not certify optional joined-value branches';assert not any(e['event_name']=='purchase'for e in events),'Nonzero/purchase path requires a separate reviewed source proof';sessions=[];users=Counter();groups=defaultdict(lambda:{'sessions':set(),'converted':set()});truth={True:True,False:False,'true':True,'false':False,'1':True,'0':False,'Y':True,'N':False}
 for row in original:
  if row['session_id']is None or row['session_start']is None:continue
  stamp=row['session_start']if type(row['session_start'])is datetime else datetime.fromisoformat(row['session_start'].replace('Z','+00:00'));day=stamp.date()
  if not date(2025,10,2)<=day<=date(2025,12,31):continue
  assert row['is_converted']in truth,'Unreviewed source Boolean representation';converted=truth[row['is_converted']];source=row['utm_source']if row['utm_source']is not None else'direct';medium=row['utm_medium']if row['utm_medium']is not None else'none';campaign=row['utm_campaign']if row['utm_campaign']is not None else'none';s=source.strip().lower();m=medium.strip().lower();group='Paid'if m in ('cpc','ppc','paid','cpm')else'Organic'if m=='organic'else'Direct'if s in ('','direct')and m in ('','none','(none)')else'Referral';key=(day,source,medium,campaign,group);users[row['visitor_id']if row['visitor_id']is not None else row['session_id']]+=1;sessions.append(row);groups[key]['sessions'].add(row['session_id'])
  if converted:groups[key]['converted'].add(row['session_id'])
 assert len(sessions)==len({r['session_id']for r in sessions})and set(users.values())=={1},'Not the independently reviewed one-session original input'
 expected=[];options={}
 for dim,g in groups.items():
  n=len(g['sessions']);converted=len(g['converted']);rate=nearest(F(converted,n),4)
  for model in MODELS:
   r=dict(zip(('attribution_date','channel','medium','campaign','channel_group'),dim));r.update(attribution_model=model,sessions=n,conversions=converted,attributed_revenue=Decimal('0.00'),conversion_rate=rate[0],assist_sessions=0);expected.append(r);options[tuple(r[k]for k in KEY)]=rate
 return expected,{'ratio_options':options,'conditions':{'raw_sessions':len(original),'raw_events':len(events),'sessions_in_window':len(sessions),'distinct_users':len(users),'converted_sessions':sum(len(g['converted'])for g in groups.values()),'group_count':len(groups),'output_rows':len(expected),'null_visitor_fallback_sessions':sum(r['visitor_id']is None for r in sessions),'purchase_event_rows':0,'joined_value_present':False,'maximum_sessions_per_user':1,'converting_path_size':1,'fractional_credit_groups':0,'true_half_rate_groups':sum(len(x)==2 for x in options.values())},'proof':'For N=1 first/last/linear/normalized 7-day decay/U-shaped credit all equal the original converted-session indicator; no other assist exists, all purchase revenue is zero. Full finite original input verified, no multi-session generalization.'}
