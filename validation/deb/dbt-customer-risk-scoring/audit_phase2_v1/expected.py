"""Independent raw-input risk derivation. No model/gold/checker imports."""
from collections import defaultdict,Counter
class InputUnknown(ValueError): pass
def trim(x):return x.strip()if isinstance(x,str)else x
def flag(x):
    if x is None:return False
    if type(x)is bool:return x
    if type(x)is int and x in (0,1):return bool(x)
    if isinstance(x,str)and x.upper()in ('TRUE','1','T','Y','YES'):return True
    if isinstance(x,str)and x.upper()in ('FALSE','0','F','N','NO'):return False
    raise InputUnknown('Undeclared chargeback representation: '+repr(x))
def derive(raw):
    groups=defaultdict(lambda:[0,0,0]);customers=[];orders=[]
    for r in raw['orders']:
        cleaned={k:trim(v)for k,v in r.items()};orders.append(cleaned);cid=cleaned['customer_id'];g=groups[cid];g[0]+=cleaned['payment_status']is not None and cleaned['payment_status'].upper()=='UNPAID';g[1]+=cleaned['status']is not None and cleaned['status'].upper()=='RETURNED';g[2]+=flag(cleaned['chargeback_flag'])
    rows=[];seen=set()
    for r in raw['customers']:
        r={k:trim(v)for k,v in r.items()};cid=r['customer_id']
        if cid is None or cid in seen:raise InputUnknown('Missing/duplicate cleaned customer source key')
        seen.add(cid);customers.append(r);first,last=r['first_name'],r['last_name']
        if first is None or last is None:raise InputUnknown('NULL customer-name concatenation policy unspecified')
        l,b,c=groups[cid];score=10*l+5*b+20*c
        rows.append({'customer_id':cid,'customer_name':first+' '+last,'late_payment_count':l,'return_count':b,'chargeback_count':c,'risk_score':score,'risk_tier':'LOW'if score<20 else'MEDIUM'if score<50 else'HIGH'if score<100 else'CRITICAL'})
    return {'stg_orders_risk':orders,'stg_customers_risk':customers,'customer_risk_scores':rows},{'source_orders':len(raw['orders']),'source_customers':len(raw['customers']),'customers':len(rows),'tiers':dict(Counter(r['risk_tier']for r in rows)),'zero_risk_customers':sum(r['risk_score']==0 for r in rows),'analysis_date':'2024-12-01; no order-date exclusion is declared by the scoring rules'}
