"""Raw-only RFM arithmetic. Equal-metric NTILE ties are not fixed to one ID order."""
from collections import defaultdict, Counter
from datetime import date, datetime
from decimal import Decimal as D
from fractions import Fraction

COLS={'int_rfm_metrics':('customer_id','recency_days','frequency','monetary'),
      'rfm_scores':('customer_id','recency_days','frequency','monetary','r_score','f_score','m_score'),
      'rfm_segments':('customer_id','customer_name','recency_days','frequency','monetary','r_score','f_score','m_score','rfm_score','rfm_segment')}
def day(x):return x.date() if isinstance(x,datetime) else x
def cents(x):
    scaled=Fraction(x)*100;lo=scaled.numerator//scaled.denominator;rem=scaled-lo
    return [D(lo)/100] if rem<Fraction(1,2) else [D(lo+1)/100] if rem>Fraction(1,2) else [D(lo)/100,D(lo+1)/100]
def segment(r,f,m):
    rules=[(r>=4 and f>=4 and m>=4,'Champions'),(r<=2 and f>=4 and m>=4,'Cannot Lose'),(f>=4 and m>=3,'Loyal Customers'),(r<=2 and f>=3 and m>=3,'At Risk'),(r>=4 and 2<=f<=4,'Potential Loyalists'),(r>=4 and f==1,'Recent Customers'),(r>=3 and f<=2 and m>=2,'Promising'),(2<=r<=3 and 2<=f<=3,'Need Attention'),(r==2 and f<=2,'About to Sleep'),(r<=2 and f<=2 and m<=2,'Hibernating'),(r==1 and f==1,'Lost')]
    return next((name for ok,name in rules if ok),'Other')
def derive(raw):
    customers={r['customer_id'].strip():r for r in raw['customers']};assert len(customers)==len(raw['customers']);groups=defaultdict(list)
    for r in raw['orders']:
        if r['ordered_at'] is not None and date(2023,1,1)<=day(r['ordered_at'])<date(2024,12,1) and r['status'] is not None and r['status'].strip() not in ('CANCELLED','RETURNED','FAILED'):
            assert r['customer_id'] is not None,'Actual NULL eligible customer requires interpretation';groups[r['customer_id'].strip()].append(r)
    expected={};missing=[]
    for cid,rs in sorted(groups.items()):
        c=customers.get(cid)
        if c is None:missing.append(cid)
        amounts=[D(r['grand_total']) for r in rs if r['grand_total'] is not None]
        expected[cid]={'customer_id':cid,'recency_days':(date(2024,12,1)-max(day(r['ordered_at'])for r in rs)).days,'frequency':len(rs),'monetary':cents(sum(amounts,D(0))),'customer_name':None if c is None else (c['first_name']or'').strip()+' '+(c['last_name']or'').strip()}
    return {'customers':expected,'facts':{'raw_orders':len(raw['orders']),'raw_customers':len(customers),'eligible_orders':sum(map(len,groups.values())),'eligible_customers':len(groups),'unmatched_customers':missing,'money_half_cent_customers':[cid for cid,r in expected.items()if len(r['monetary'])==2],'metric_ties':{k:sum(n for n in Counter(str(r[k])for r in expected.values()).values()if n>1)for k in ['recency_days','frequency','monetary']},'scope':'Full actual raw population; score ordering is independently checked by equal-metric group bucket multisets, never by fixing the example customer_id sort.'}}
