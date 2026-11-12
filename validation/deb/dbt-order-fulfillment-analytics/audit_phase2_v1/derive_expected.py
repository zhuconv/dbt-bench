import sys
sys.dont_write_bytecode=True
import pathlib,json,hashlib,datetime,collections,decimal
D=decimal.Decimal
RAW_FIELDS={
'ORDERS':'order_id order_number customer_id order_type order_source channel_id currency_code status payment_status fulfillment_status subtotal discount_total shipping_total tax_total grand_total ordered_at shipped_at delivered_at cancelled_at warehouse_id carrier_code service_level created_at updated_at'.split(),
'ORDER_LINES':'order_line_id order_id line_number variant_id product_id sku product_name quantity_ordered quantity_shipped quantity_returned unit_price discount_amount tax_amount line_total status created_at updated_at'.split(),
'SHIPMENTS':'shipment_id shipment_number order_id warehouse_id carrier_id shipping_method_id tracking_number status shipped_at delivered_at shipping_cost weight created_at updated_at'.split(),
'SHIPMENT_LINES':'shipment_line_id shipment_id order_line_id quantity_shipped created_at'.split(),
'ORDER_STATUS_HISTORY':'history_id order_id old_status new_status changed_by change_reason notes changed_at'.split(),
'RETURNS':'return_id return_number order_id customer_id status return_type refund_method refund_amount requested_at received_at processed_at notes created_at updated_at'.split()}
STAGING={n:'staging.stg_'+n.lower()for n in RAW_FIELDS}
LIFE='order_id order_number customer_id order_type order_source status grand_total ordered_at first_status_change_at time_to_first_ship_hours time_to_delivery_hours status_change_count'.split()
SHIP='shipment_id shipment_number order_id warehouse_id carrier_id status shipped_at delivered_at items_in_shipment total_quantity_shipped shipping_cost transit_time_hours'.split()
FUL='order_id order_number ordered_at total_lines total_quantity_ordered total_quantity_shipped total_quantity_returned fulfillment_pct is_fully_fulfilled is_partially_fulfilled'.split()
REV='order_id order_date warehouse_id line_item_count gross_revenue shipping_revenue total_revenue'.split()
WARE='warehouse_id order_date total_orders total_order_value total_shipments orders_fully_fulfilled orders_partially_fulfilled fulfillment_rate avg_time_to_ship_hours avg_time_to_delivery_hours'.split()
RET='return_type return_month total_returns total_refund_amount avg_refund_amount returns_processed returns_rejected processing_rate avg_processing_time_days'.split()
CAR='carrier_id total_shipments shipments_delivered shipments_failed in_transit_shipments delivery_rate failed_shipment_rate total_shipping_cost avg_shipping_cost avg_transit_time_hours_delivered_only on_time_delivery_count'.split()
FLOW='old_status new_status transition_count unique_orders avg_time_in_old_status_hours pct_of_all_transitions'.split()
DAY='order_date order_count line_item_count gross_revenue shipping_revenue total_revenue avg_order_value'.split()
def encoded(x):return json.dumps(x,default=str,sort_keys=True,indent=2)+'\n'
def digest(x):return hashlib.sha256(encoded(x).encode()).hexdigest()
def dt(v):return datetime.datetime.fromisoformat(str(v).replace('Z','+00:00'))if v is not None else None
def date(v):return dt(v).date()if v is not None else None
def mon(v):return date(v).replace(day=1)if v is not None else None
def sm(v):
 a=[D(str(x))for x in v if x is not None];return sum(a,D(0))if a else None
def avg(v):
 a=[D(str(x))for x in v if x is not None];return sum(a,D(0))/len(a)if a else None
def mn(v):return min([x for x in v if x is not None],default=None)
def diff(a,b,den=3600):
 if a is None or b is None:return None
 # DATEDIFF(second) counts boundaries; source clock fractional parts are profiled separately.
 a=dt(a).replace(microsecond=0);b=dt(b).replace(microsecond=0);return D(str((b-a).total_seconds()))/D(den)
def groups(rows,key):
 d=collections.defaultdict(list)
 for r in rows:d[key(r)].append(r)
 return d
def read_raw(c):return {n:[dict(zip(cols,r))for r in c.execute('SELECT '+','.join(cols)+' FROM ORDERS.'+n+' ORDER BY '+cols[0]).fetchall()]for n,cols in RAW_FIELDS.items()}
def derive(raw):
 outputs={STAGING[n]:rows for n,rows in raw.items()};orders=raw['ORDERS'];lines=groups(raw['ORDER_LINES'],lambda r:r['order_id']);shipments=groups(raw['SHIPMENTS'],lambda r:r['order_id']);slines=groups(raw['SHIPMENT_LINES'],lambda r:r['shipment_id']);history=groups(raw['ORDER_STATUS_HISTORY'],lambda r:r['order_id']);lifecycle=[];shipment_perf=[];fulfillment=[];revenue=[]
 for o in orders:
  oid=o['order_id'];hs=history[oid];ss=shipments[oid];first=mn([dt(r['shipped_at'])for r in ss]);r={c:o[c]for c in LIFE[:8]};r.update(first_status_change_at=mn([dt(z['changed_at'])for z in hs]),time_to_first_ship_hours=diff(o['ordered_at'],first),time_to_delivery_hours=diff(o['ordered_at'],o['delivered_at']),status_change_count=len(hs));lifecycle.append(r)
  ls=lines[oid];qty=sm([r['quantity_ordered']for r in ls])or D(0);shipped=sm([r['quantity_shipped']for r in ls])or D(0);returned=sm([r['quantity_returned']for r in ls])or D(0);fulfillment.append(dict(zip(FUL,[oid,o['order_number'],o['ordered_at'],len(ls),qty,shipped,returned,shipped/qty*100 if qty>0 else D(0),int(qty>0 and shipped>=qty),int(shipped>0 and shipped<qty)])))
  if o['status']is not None and o['status']!='CANCELLED'and o['ordered_at']is not None and ls:
   gross=sm([r['line_total']for r in ls]);shipping=D(str(o['shipping_total']or 0));revenue.append(dict(zip(REV,[oid,date(o['ordered_at']),o['warehouse_id'],len(ls),gross,shipping,gross+shipping if gross is not None else None])))
 for s in raw['SHIPMENTS']:
  ls=slines[s['shipment_id']];shipment_perf.append(dict(zip(SHIP,[*[s[c]for c in SHIP[:8]],len(ls),sm([r['quantity_shipped']for r in ls])or D(0),s['shipping_cost'],diff(s['shipped_at'],s['delivered_at'])])))
 outputs.update({'intermediate.int_order_lifecycle':lifecycle,'intermediate.int_shipment_performance':shipment_perf,'intermediate.int_order_fulfillment_status':fulfillment,'intermediate.int_order_revenue_summary':revenue})
 ret=[]
 for key,rs in groups(raw['RETURNS'],lambda r:(r['return_type'],mon(r['requested_at']))).items():
  processed=sum(r['status']=='PROCESSED'for r in rs);ret.append(dict(zip(RET,[*key,len(rs),sm([r['refund_amount']for r in rs]),avg([r['refund_amount']for r in rs]),processed,sum(r['status']=='REJECTED'for r in rs),D(processed)/len(rs)*100,avg([diff(r['requested_at'],r['processed_at'],86400)for r in rs if r['status']=='PROCESSED'])])))
 carriers=[]
 for key,rs in groups(shipment_perf,lambda r:r['carrier_id']).items():
  delivered=[r for r in rs if r['status']=='DELIVERED'];failed=sum(r['status']=='FAILED'for r in rs);carriers.append(dict(zip(CAR,[key,len(rs),len(delivered),failed,sum(r['status']=='IN_TRANSIT'for r in rs),D(len(delivered))/len(rs)*100,D(failed)/len(rs)*100,sm([r['shipping_cost']for r in rs]),avg([r['shipping_cost']for r in rs]),avg([r['transit_time_hours']for r in delivered]),sum(r['transit_time_hours']is not None and r['transit_time_hours']<=120 for r in delivered)])))
 daily=[]
 for key,rs in groups(revenue,lambda r:r['order_date']).items():
  total=sm([r['total_revenue']for r in rs]);daily.append(dict(zip(DAY,[key,len(rs),sum(r['line_item_count']for r in rs),sm([r['gross_revenue']for r in rs]),sm([r['shipping_revenue']for r in rs]),total,total/len(rs)])))
 # Explicitly retain two status-history interpretations: filter-before-lag vs all-history-lag.
 flows={};transition_profiles={}
 for filter_before in [True,False]:
  elapsed=[];ties=[]
  for oid,rs0 in history.items():
   rs=[r for r in rs0 if not filter_before or(r['old_status']is not None and r['new_status']is not None)];rs.sort(key=lambda r:(r['changed_at']is None,str(r['changed_at']),r['history_id']));prev=None
   counts=collections.Counter(str(r['changed_at'])for r in rs)
   if any(n>1 for n in counts.values()):ties.append(oid)
   for r in rs:
    if r['old_status']is not None and r['new_status']is not None:elapsed.append({**r,'elapsed':diff(prev['changed_at'],r['changed_at'])if prev else None})
    prev=r
  result=[]
  for key,rs in groups(elapsed,lambda r:(r['old_status'],r['new_status'])).items():result.append(dict(zip(FLOW,[*key,len(rs),len({r['order_id']for r in rs if r['order_id']is not None}),avg([r['elapsed']for r in rs]),D(len(rs))/len(elapsed)*100])))
  label='filtered_history'if filter_before else'all_history';flows[label]=result;transition_profiles[label]={'included_transitions':len(elapsed),'orders_with_timestamp_ties':len(ties)}
 # Warehouse output uses retained revenue-eligible cohort only for documenting original implementation;
 # independent all-order count below is the explicit business check, with non-NULL keys only.
 lm={r['order_id']:r for r in lifecycle};fm={r['order_id']:r for r in fulfillment};ship_by_day=groups([s for s in raw['SHIPMENTS']if s['shipped_at']is not None],lambda r:(r['warehouse_id'],date(r['shipped_at'])));ware=[]
 for key,rs in groups(revenue,lambda r:(r['warehouse_id'],r['order_date'])).items():
  fully=sum(fm[r['order_id']]['is_fully_fulfilled']for r in rs);partial=sum(fm[r['order_id']]['is_partially_fulfilled']for r in rs);ns=len(ship_by_day[key])if key[0]is not None else 0;ware.append(dict(zip(WARE,[*key,len(rs),sm([r['total_revenue']for r in rs]),ns,fully,partial,D(fully)/len(rs)*100,avg([lm[r['order_id']]['time_to_first_ship_hours']for r in rs]),avg([lm[r['order_id']]['time_to_delivery_hours']for r in rs])])) )
 allcounts=[{'warehouse_id':k[0],'order_date':k[1],'total_orders':len({r['order_id']for r in rs if r['order_id']is not None})}for k,rs in groups([o for o in orders if o['warehouse_id']is not None and o['ordered_at']is not None],lambda r:(r['warehouse_id'],date(r['ordered_at']))).items()]
 outputs.update({'marts.mart_return_analysis':ret,'marts.mart_carrier_performance':carriers,'marts.mart_daily_revenue':daily,'marts.mart_order_status_flow':flows['filtered_history'],'marts.mart_fulfillment_metrics':ware})
 return {'outputs':outputs,'warehouse_independent_all_order_counts':allcounts,'status_flow_policy_alternative':flows['all_history'],'profiles':{'raw_counts':{n:len(r)for n,r in raw.items()},'revenue_eligible_orders':len(revenue),'warehouse_all_order_groups':len(allcounts),'warehouse_revenue_cohort_groups':len(ware),'status_flow':transition_profiles,'raw_timestamp_fractional_rows':{n:sum(any(dt(v).microsecond!=0 for c,v in r.items()if v is not None and c in['ordered_at','shipped_at','delivered_at','changed_at','requested_at','processed_at'])for r in rows)for n,rows in raw.items()}},'scope':'OriginalrawORDERS6tables; sixstagingreferenceprojectionsandfourintermediate/fivemartdatasets independentlyderivedusingPythonjoins/grouping/Decimal/datearithmetics. Warehousepublishedimplementationcohort and status-flow filter-before-lag are disclosed conditional reproductions, not businesspolicyendorsement; independent allrawordercountsseparatelyproveknownwarehouseerror. ReturnLinesunusedbecauseinstructiondeclaresnomodelrequiringitsfields.'}
if __name__=='__main__':
 import duckdb
 with duckdb.connect('/app/database/retail.duckdb',read_only=True)as c:raw=read_raw(c)
 out=derive(raw);out['raw_input_sha256']=digest(raw);pathlib.Path('/review/fulfillment-analytics-original-derived-v2.json').write_text(encoded(out));print(out['profiles'])
