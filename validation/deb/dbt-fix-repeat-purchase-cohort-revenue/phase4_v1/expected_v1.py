"""V2 repeat-cohort contract: date filter precedes dedup; NULL IDs stay separate."""
from collections import defaultdict
from datetime import date
from decimal import Decimal as D
COLS={'rpt_repeat_purchase_cohort_revenue_fixed':('cohort_month','months_since_cohort','cohort_size','active_customers','orders','revenue','cumulative_revenue')}
def month(x):return x.year*12+x.month-1
def daymonth(i):return date(i//12,i%12+1,1)
def derive(raw):
 source=raw['enriched'];groups=defaultdict(list)
 for index,r in enumerate(source):
  if r['ordered_at'] is not None:groups[('order',r['order_id']) if r['order_id'] is not None else ('null_order_row',index)].append(r)
 orders={}
 for oid,rs in groups.items():
  ids=[r['customer_id'] for r in rs if r['customer_id'] is not None];orders[oid]={'customer_id':min(ids) if ids else None,'ordered_at':min(r['ordered_at'] for r in rs),'grand_total':sum((D(r['grand_total']) for r in rs if r['grand_total'] is not None),D(0))}
 first={}
 for oid,r in orders.items():
  identity=('customer',r['customer_id']) if r['customer_id'] is not None else ('anonymous',oid);first[identity]=min(first.get(identity,month(r['ordered_at'])),month(r['ordered_at']))
 members=defaultdict(set);buckets=defaultdict(list)
 for oid,r in orders.items():
  cid=r['customer_id'];identity=('customer',cid) if cid is not None else ('anonymous',oid);cohort=first[identity]
  if cid is not None:members[cohort].add(cid)
  buckets[cohort,month(r['ordered_at'])-cohort].append((cid,oid,r['grand_total']))
 rows=[];running=defaultdict(lambda:D(0))
 for (cohort,offset),rs in sorted(buckets.items()):
  amount=sum((r[2] for r in rs),D(0));running[cohort]+=amount
  rows.append(dict(zip(COLS['rpt_repeat_purchase_cohort_revenue_fixed'],(daymonth(cohort),offset,len(members[cohort]),len({r[0] for r in rs if r[0] is not None}),len({r[1] for r in rs if r[1][0]=='order'}),amount,running[cohort]))))
 correct={'rows':rows,'orders':orders,'source_order_total':sum(k[0]=='order' for k in orders),'source_revenue_total':sum((r['grand_total'] for r in orders.values()),D(0))}
 return {'outputs':{'rpt_repeat_purchase_cohort_revenue_fixed':rows},'correct':correct,'facts':{'raw_rows':len(source),'dated_rows':sum(r['ordered_at'] is not None for r in source),'records_after_dedup':len(orders),'null_id_records':sum(k[0]=='null_order_row' for k in orders),'buckets':len(rows),'source_distinct_nonnull_orders':correct['source_order_total'],'source_revenue_total':correct['source_revenue_total'],'scope':'Source-only Python grouping: filter NULL timestamps before MIN/MIN/SUM for non-NULL IDs; NULL IDs retain each input record, are excluded only from order counts; anonymous customers never count in population but retain revenue.'}}
