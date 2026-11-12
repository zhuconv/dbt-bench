"""Independent original-session Python aggregation. No SQL/gold expected inputs."""
from collections import defaultdict,Counter
from fractions import Fraction
from decimal import Decimal

def nearest(value,places):
 scale=10**places;n=Fraction(value)*scale;q,r=divmod(n.numerator,n.denominator)
 return tuple(Decimal(v)/scale for v in([q,q+1]if 2*r==n.denominator else[q+(2*r>n.denominator)]))
def derive(inputs,decode):
 source=inputs['relations']['main.WEB_SESSIONS'];cols=[v['name'].lower()for v in source['schema']];raw=[dict(zip(cols,map(decode,row)))for row in source['rows']];assert len(raw)==5029 and all(all(v is not None for v in r.values())for r in raw)
 assert len({r['session_id']for r in raw})==len(raw);scores={}
 for metric in ['page_views','duration_seconds']:
  ordered=sorted(raw,key=lambda r:(r[metric],r['session_id']));q,remainder=divmod(len(raw),5);labels=[]
  for bucket in range(1,6):labels += [bucket]*(q+(bucket<=remainder))
  scores[metric]={r['session_id']:s for r,s in zip(ordered,labels)}
 visitors=defaultdict(list)
 for r in raw:visitors[r['visitor_id']].append(r)
 visits={}
 for rows in visitors.values():
  assert len({r['session_start']for r in rows})==len(rows),'Actual time ties require separate legal-order investigation'
  for i,r in enumerate(sorted(rows,key=lambda r:r['session_start']),1):visits[r['session_id']]=i
 fact=[];velocities={}
 for r in raw:
  key=r['session_id'];es=scores['page_views'][key];ds=scores['duration_seconds'][key];velocity=Fraction(0)if r['duration_seconds']<60 else Fraction(r['page_views']*60,r['duration_seconds']);velocities[key]=velocity;fact.append({**r,'is_bounce':int(r['page_views']==1),'is_converted':int(r['is_converted']),'engagement_score':es,'duration_score':ds,'quality_tier':'Premium'if min(es,ds)>=4 else'High'if max(es,ds)>=4 else'Medium'if min(es,ds)>=2 else'Low','velocity_category':None if r['duration_seconds']<60 else'Fast'if velocity>2 else'Normal'if velocity>=1 else'Slow','visit_number':visits[key],'is_returning_visitor':int(visits[key]>1)})
 devices=defaultdict(list)
 for r in fact:devices[r['device_type']].append(r)
 summary=[];rounded_options={};summary_velocity_alternatives={}
 for device,rows in sorted(devices.items()):
  n=len(rows);converted=sum(r['is_converted']for r in rows);bounce=sum(r['is_bounce']for r in rows);returning=sum(r['is_returning_visitor']for r in rows);ratios={'conversion_rate':(Fraction(converted,n),4),'avg_duration':(Fraction(sum(r['duration_seconds']for r in rows),n),2),'avg_page_views':(Fraction(sum(r['page_views']for r in rows),n),2),'bounce_rate':(Fraction(bounce,n),4),'returning_visitor_rate':(Fraction(returning,n),4),'avg_engagement_velocity':(sum(velocities[r['session_id']]for r in rows)/n,4)};options={k:nearest(v,p)for k,(v,p)in ratios.items()};rounded_options[('rpt_session_summary',device)]=options;summary_velocity_alternatives[device]={'full_fraction':options['avg_engagement_velocity'],'four_decimal_fact':nearest(sum(Fraction(nearest(velocities[r['session_id']],4)[-1])for r in rows)/n,4)};summary.append({'device_type':device,'total_sessions':n,'total_conversions':converted,'premium_sessions':sum(r['quality_tier']=='Premium'for r in rows),'high_sessions':sum(r['quality_tier']=='High'for r in rows),**{k:v[-1]for k,v in options.items()}})
 segments=defaultdict(list)
 for key,rows in visitors.items():segments['Power User'if len(rows)>=5 else'Regular'if len(rows)>=2 else'One-Time'].append(rows)
 result=[]
 for segment,groups in sorted(segments.items()):
  count=len(groups);n=sum(map(len,groups));conversions=sum(int(r['is_converted'])for rows in groups for r in rows);options={'avg_sessions_per_visitor':nearest(Fraction(n,count),2),'conversion_rate':nearest(Fraction(conversions,n),4)};rounded_options[('rpt_visitor_segments',segment)]=options;result.append({'visitor_segment':segment,'visitor_count':count,'total_sessions':n,'total_conversions':conversions,**{k:v[-1]for k,v in options.items()}})
 return {'fct_session_quality':fact,'rpt_session_summary':summary,'rpt_visitor_segments':result}, {'velocities':velocities,'rounding_options':rounded_options,'summary_velocity_alternatives':summary_velocity_alternatives,'raw':raw}
