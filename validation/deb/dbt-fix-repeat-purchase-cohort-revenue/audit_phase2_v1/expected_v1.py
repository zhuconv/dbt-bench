"""Independent per-order MIN/MIN/SUM before rejecting undated orders."""
from collections import defaultdict
from datetime import date,datetime
from decimal import Decimal as D
COLS={'rpt_repeat_purchase_cohort_revenue_fixed':('cohort_month','months_since_cohort','cohort_size','active_customers','orders','revenue','cumulative_revenue')}
def month(x):return x.year*12+x.month-1
def daymonth(i):return date(i//12,i%12+1,1)
def derive_policy(rows,filter_rows_first=False):
    grouped=defaultdict(list)
    for r in rows:
        if filter_rows_first and r['ordered_at']is None:continue
        grouped[r['order_id']].append(r)
    orders={};discarded=[]
    for oid,rs in grouped.items():
        dates=[r['ordered_at']for r in rs if r['ordered_at']is not None]
        if not dates:discarded.append(oid);continue
        ids=[r['customer_id']for r in rs if r['customer_id']is not None];orders[oid]={'customer_id':min(ids)if ids else None,'ordered_at':min(dates),'grand_total':sum((D(r['grand_total'])for r in rs if r['grand_total']is not None),D(0))}
    first={}
    for oid,r in orders.items():
        key=('customer',r['customer_id'])if r['customer_id']is not None else('anonymous_order',oid);first[key]=min(first.get(key,month(r['ordered_at'])),month(r['ordered_at']))
    members=defaultdict(set);buckets=defaultdict(list)
    for oid,r in orders.items():
        cid=r['customer_id'];identity=('customer',cid)if cid is not None else('anonymous_order',oid);cohort=first[identity]
        if cid is not None:members[cohort].add(cid)
        buckets[cohort,month(r['ordered_at'])-cohort].append((cid,oid,r['grand_total']))
    output=[];cumulative=defaultdict(lambda:D(0))
    for(cohort,offset),rs in sorted(buckets.items()):
        money=sum((r[2]for r in rs),D(0));assert money==money.quantize(D('.01'));cumulative[cohort]+=money;output.append(dict(zip(COLS['rpt_repeat_purchase_cohort_revenue_fixed'],[daymonth(cohort),offset,len(members[cohort]),len({r[0]for r in rs if r[0]is not None}),len({r[1]for r in rs if r[1]is not None}),money,cumulative[cohort]])))
    return {'rows':output,'orders':orders,'discarded_undated_order_ids':discarded,'source_order_total':len(orders),'source_revenue_total':sum((r['grand_total']for r in orders.values()),D(0))}
def derive(raw):
    rs=raw['enriched'];correct=derive_policy(rs);early=derive_policy(rs,True);groups=defaultdict(list)
    for r in rs:groups[r['order_id']].append(r)
    mixed={k:v for k,v in groups.items()if any(r['ordered_at']is None for r in v)and any(r['ordered_at']is not None for r in v)}
    money=sum((D(r['grand_total'])for vs in mixed.values()for r in vs if r['ordered_at']is None and r['grand_total']is not None),D(0));changed=[oid for oid in mixed if correct['orders'][oid]['customer_id']!=early['orders'][oid]['customer_id']]
    return {'outputs':{'rpt_repeat_purchase_cohort_revenue_fixed':correct['rows']},'correct':correct,'early_row_filter_alternative':early,'facts':{'raw_rows':len(rs),'distinct_raw_orders':len(groups),'valid_deduplicated_orders':len(correct['orders']),'required_buckets':len(correct['rows']),'mixed_null_date_order_groups':len(mixed),'mixed_null_date_amount':money,'changed_min_customer_orders':changed,'all_null_date_orders_ignored':len(correct['discarded_undated_order_ids']),'dedup_before_date_filter_total':correct['source_revenue_total'],'filter_before_dedup_total':early['source_revenue_total'],'scope':'MIN customer and timestamp plus SUM money over every source row of an order; ignore an order only when its resulting minimum date is NULL. Existing early-row-filter alternative separately retained to expose actual sequencing effects; no native/gold arithmetic invoked.'}}
