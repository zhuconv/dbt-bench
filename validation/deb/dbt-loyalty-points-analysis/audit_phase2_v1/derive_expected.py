"""Raw loyalty events -> independent per-program counts and point arithmetic."""
from collections import defaultdict
from decimal import Decimal,ROUND_HALF_UP,ROUND_FLOOR,ROUND_CEILING
D=Decimal

def trim(v):return v.strip()if isinstance(v,str)else v
def records(inputs,table,decode):
 item=inputs['relations'][table];names=[v['name'].casefold()for v in item['schema']];return[dict(zip(names,map(decode,row)))for row in item['rows']]
def derive(inputs,decode):
 stages={role:[{k:trim(v)for k,v in r.items()}for r in records(inputs,table,decode)]for role,table in [('transactions','main.LOYALTY_POINTS_TRANSACTIONS'),('programs','main.LOYALTY_PROGRAMS'),('customers','main.CUSTOMERS')]};groups=defaultdict(list)
 for r in stages['transactions']:groups[r['program_id']].append(r)
 programs=defaultdict(list)
 for r in stages['programs']:programs[r['program_id']].append(r)
 intermediate=[];final=[];exact={}
 for pid,rows in groups.items():
  count=len({r['customer_id']for r in rows if r['customer_id']is not None});earned=sum((r['points']for r in rows if r['transaction_type']in('EARN','BONUS')and r['points']is not None),0);redeemed=sum((abs(r['points'])for r in rows if r['transaction_type']=='REDEEM'and r['points']is not None),0);expired=sum((abs(r['points'])for r in rows if r['transaction_type']=='EXPIRE'and r['points']is not None),0);m={'program_id':pid,'member_count':count,'points_earned':earned,'points_redeemed':redeemed,'points_expired':expired};intermediate.append(m);balance=earned-redeemed-expired;rate=D(100*redeemed)/earned if earned else None;average=D(balance)/count if count else None
  for program in programs[pid]:
   final.append({**m,'program_name':program['program_name'],'points_balance':balance,'redemption_rate':rate,'avg_points_per_member':average});exact[pid]={'rate':rate,'average':average,'integer_floor':int(average.to_integral_value(rounding=ROUND_FLOOR))if average is not None else None,'integer_ceil':int(average.to_integral_value(rounding=ROUND_CEILING))if average is not None else None}
 return {'stages':stages,'intermediate':intermediate,'final':final,'exact':exact,'profile':{'transactions':len(stages['transactions']),'programs':len(stages['programs']),'customers':len(stages['customers']),'zero_denominators':[k for k,v in exact.items()if v['rate']is None or v['average']is None],'transaction_types':{t:sum(r['transaction_type']==t for r in stages['transactions'])for t in sorted({r['transaction_type']for r in stages['transactions']})}}}
