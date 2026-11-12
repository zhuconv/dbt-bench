"""Independent source recomputation with explicit, non-exhaustive event policies."""
from collections import Counter,defaultdict
from fractions import Fraction as F
COLS={'int_session_funnel':['session_id','visitor_id','customer_id','session_start','has_product_view','has_add_to_cart','has_purchase','funnel_stage'],'funnel_conversion_rates':['total_sessions','sessions_with_view','sessions_with_cart','sessions_with_purchase','view_rate','view_to_cart_rate','cart_to_purchase_rate','overall_conversion_rate'],'funnel_dropoff_analysis':['dropoff_stage','session_count','dropoff_rate']}
def trim(x):return x.strip()if isinstance(x,str)else x
def derive(raw):
 events=raw['events'];complete={trim(r['session_id'])for r in raw['carts']if trim(r['status'])=='CONVERTED'};profile=Counter(str(s['is_converted'])for s in raw['sessions']);unknown=[t for t in profile if t.strip().lower()not in['true','false','1','0','y','n','yes','no']];policies={};norm=Counter((r['event_type'],r['event_name'])for r in events)
 for mode in ['full_type_only','name_supported_aliases','all_shorthand']:
  flags=defaultdict(set)
  for e in events:
   t=e['event_type'];n=trim(e['event_name']);v=t=='view_product';a=t=='add_to_cart'
   if mode=='name_supported_aliases':v|=t=='v'and n=='View Product';a|=t=='a'and n=='Add To Cart'
   if mode=='all_shorthand':v|=t in['v','view'];a|=t=='a'
   if v:flags[trim(e['session_id'])].add('v')
   if a:flags[trim(e['session_id'])].add('a')
  rows=[]
  for s in raw['sessions']:
   sid=trim(s['session_id']);v=int('v'in flags[sid]);a=int('a'in flags[sid]);p=int(str(s['is_converted']).strip().lower()in['true','1','y','yes']or sid in complete);rows.append([sid,trim(s['visitor_id']),trim(s['customer_id']),s['session_start'],v,a,p,'PURCHASE'if p else'CART'if a else'VIEW'if v else'NONE'])
  n=len(rows);v=sum(r[4]for r in rows);a=sum(r[5]for r in rows);p=sum(r[6]for r in rows);ratio=lambda a,b:str(F(a,b))if b else None;summary=[[n,v,a,p,ratio(v,n),ratio(a,v),ratio(p,a),ratio(p,n)]];drops=[[label,count,ratio(count,n)]for label,count in [('BEFORE_VIEW',sum(not r[4]for r in rows)),('VIEW_TO_CART',sum(r[4]and not r[5]for r in rows)),('CART_TO_PURCHASE',sum(r[5]and not r[6]for r in rows))]];policies[mode]={'int_session_funnel':rows,'funnel_conversion_rates':summary,'funnel_dropoff_analysis':drops}
 return {'columns':COLS,'policies':policies,'profile':{'rows':len(raw['sessions']),'duplicate_trimmed_session_ids':len(raw['sessions'])-len({trim(r['session_id'])for r in raw['sessions']}),'conversion_tokens':dict(profile),'unknown_boolean_tokens':unknown,'ambiguous_event_rows':sum(v for(t,n),v in norm.items()if t in['v','view','a']),'event_type_name_profile':[{'event_type':t,'event_name':n,'rows':v}for(t,n),v in norm.items()]},'scope':'Actual fixed-input flags and exact ratios for three disclosed event readings. These readings are not an exhaustive legal set, and output matches cannot choose a unique business mapping.'}
