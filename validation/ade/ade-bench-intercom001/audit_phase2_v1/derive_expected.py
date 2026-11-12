"""Independent raw history aggregation; conventions remain named hypotheses."""
from datetime import datetime
from decimal import Decimal,ROUND_HALF_UP
THREADS=['conversation_id','conversation_created_at','total_conversation_parts','first_contact_reply_at','first_assignment_at','first_admin_response_at','first_reopen_at','last_assignment_at','last_contact_reply_at','last_admin_response_at','last_reopen_at','total_assignments','total_reopens']
METRICS=['conversation_id','conversation_created_at','total_reopens','total_conversation_parts','total_assignments','first_contact_reply_at','first_assignment_at','first_admin_response_at','first_reopen_at','last_assignment_at','last_contact_reply_at','last_admin_response_at','last_reopen_at','first_close_at','last_close_at','time_to_first_assignment_minutes','time_to_first_response_minutes','time_to_first_close_minutes','time_to_last_close_minutes']
def stamp(v):return v if v is None or isinstance(v,datetime)else datetime.fromisoformat(v)
def derive(raw,admin_assignment=True,active_close=False,elapsed=False,close_creation=False):
 conv=raw['conversation_history_data'];parts=raw['conversation_part_history_data'];active=[r for r in conv if r['_fivetran_active']is not False];rows=[];metric=[]
 for c in active:
  ev=[r for r in parts if r['conversation_id']==c['id']and r['_fivetran_active']is not False];families={'contact_reply':[r for r in ev if r['part_type']=='comment'and r['author_type']in('lead','user')],'assignment':[r for r in ev if'assignment'in(r['part_type']or'')],'admin_response':[r for r in ev if r['part_type']in(('comment','assignment')if admin_assignment else('comment',))and r['author_type']=='admin'and r['body']is not None],'reopen':[r for r in ev if r['part_type']=='open']};x={'conversation_id':c['id'],'conversation_created_at':stamp(c['created_at']),'total_conversation_parts':sum(r['id']is not None for r in ev),'total_assignments':len(families['assignment']),'total_reopens':len(families['reopen'])}
  for name,items in families.items():
   ts=[stamp(r['created_at'])for r in items if r['created_at']is not None];x['first_'+name+'_at']=min(ts,default=None);x['last_'+name+'_at']=max(ts,default=None)
  rows.append({n:x[n]for n in THREADS});closes=[stamp(r['created_at'])for r in parts if r['conversation_id']==c['id']and r['part_type']=='close'and r['created_at']is not None and(not active_close or r['_fivetran_active']is not False)];x['first_close_at']=min(closes,default=None);x['last_close_at']=max(closes,default=None)
  for name,start,end in [('first_assignment','conversation_created_at','first_assignment_at'),('first_response','conversation_created_at','first_admin_response_at'),('first_close','conversation_created_at'if close_creation else'first_contact_reply_at','first_close_at'),('last_close','conversation_created_at'if close_creation else'first_contact_reply_at','last_close_at')]:
   a,b=x[start],x[end];v=None
   if a is not None and b is not None:
    if not elapsed:a=a.replace(microsecond=0);b=b.replace(microsecond=0)
    delta=b-a;seconds=Decimal(delta.days*86400+delta.seconds)+Decimal(delta.microseconds)/1000000;v=(seconds/60).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
   x['time_to_'+name+'_minutes']=v
  metric.append({n:x[n]for n in METRICS})
 return {'intercom__threads':rows,'intercom__conversation_metrics':metric}
