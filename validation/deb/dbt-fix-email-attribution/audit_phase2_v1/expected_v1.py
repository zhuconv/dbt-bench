"""Independent Python attribution; never reads candidate SQL or native expected data."""
from collections import defaultdict
from datetime import date,datetime,timedelta
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP,localcontext
class UnderspecifiedInput(ValueError):pass
KEYS=('attribution_date','channel','medium','campaign')
def day(v):
 if v is None:return None
 if isinstance(v,datetime):return v.date()
 if isinstance(v,date):return v
 return date.fromisoformat(str(v)[:10])
def truth(v):
 if v is None:return False
 if type(v)is bool:return v
 s=str(v).strip().lower()
 if s in ('1','1.0','1.00','true','t','yes','y'):return True
 if s in ('0','0.0','0.00','false','f','no','n','','null'):return False
 raise UnderspecifiedInput('Unspecified conversion flag representation: '+repr(v))
def money(v):
 if v is None:return Decimal(0)
 try:x=Decimal(str(v))
 except InvalidOperation as exc:raise UnderspecifiedInput('Invalid purchase amount')from exc
 if not x.is_finite():raise UnderspecifiedInput('Nonfinite purchase amount')
 return x
def rounded(x,places):
 with localcontext()as ctx:ctx.prec=80;return x.quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP)
def derive(kind,raw,joined_available=False):
 sessions=raw['sessions'];dates=[day(r['session_start'])for r in sessions if r['session_start']is not None]
 if not dates:raise UnderspecifiedInput('Empty session-date source requires separately reviewed empty-result policy')
 hi=date(2026,1,2)if kind=='email'else max(dates);lo=date(2025,10,4)if kind=='email'else hi-timedelta(days=90)
 source='joined'if joined_available else'events';events=raw.get(source,[]);eventname='t2_event_name'if joined_available else'event_name';amount='t2_event_value'if joined_available else'event_value';revenue=defaultdict(Decimal);purchase_events=0
 for e in events:
  if e[eventname]!='purchase' or e['session_id']is None:continue
  ident=str(e['session_id']).strip()if kind=='email'else e['session_id'];revenue[ident]+=money(e[amount]);purchase_events+=1
 groups=defaultdict(lambda:{'sessions':set(),'converted':set()});ident_dimensions=defaultdict(set);flags=set();invalid_ids=0;eligible=0
 for r in sessions:
  d=day(r['session_start'])
  if d is None or not lo<=d<=hi:continue
  ident=r['session_id']
  if ident is None:invalid_ids+=1;continue
  if kind=='email':
   ident=str(ident).strip()
   if not ident:invalid_ids+=1;continue
  dims=[]
  for name,default in [('utm_source','direct'),('utm_medium','none'),('utm_campaign','none')]:
   value=r[name];dims.append((str(value).strip()or default)if kind=='email'and value is not None else default if value is None else value)
  key=(d,*dims);eligible+=1;ident_dimensions[ident].add(key);g=groups[key];g['sessions'].add(ident);flags.add(str(r['is_converted']))
  if truth(r['is_converted']):g['converted'].add(ident)
 conflicts={k:len(v)for k,v in ident_dimensions.items()if len(v)>1}
 if conflicts:raise UnderspecifiedInput('Same session assigned multiple dimension groups: '+repr(list(conflicts.items())[:3]))
 rows=[]
 for key,g in sorted(groups.items()):
  n=len(g['sessions']);c=len(g['converted']);amount=sum((revenue[s]for s in g['converted']),Decimal(0));amount=max(amount,Decimal(0))if kind=='email'else amount
  with localcontext()as ctx:ctx.prec=80;rate=rounded(Decimal(c)/n if n else Decimal(0),4)
  rows.append(dict(zip(KEYS,key))|{'sessions':n,'conversions':c,'attributed_revenue':rounded(amount,2),'conversion_rate':rate})
 return rows,{'source_sessions':len(sessions),'source_events':len(events),'selected_revenue_source':source,'purchase_events':purchase_events,'date_min':str(lo),'date_max':str(hi),'eligible_source_rows':eligible,'eligible_distinct_sessions':len(ident_dimensions),'discarded_invalid_ids':invalid_ids,'groups':len(rows),'converted_sessions':sum(r['conversions']for r in rows),'observed_conversion_flags':sorted(flags),'scope':'No SQL/reference/gold dependency. Current input has no ambiguous cross-dimension duplicate-session membership. Source-selection counterfactual correctness is separate from current-data aggregate equality.'}
