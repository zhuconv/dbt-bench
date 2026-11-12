"""Independent raw warehouse aggregation; unspecified NTILE ties remain admissible."""
import collections,datetime
from fractions import Fraction
from decimal import Decimal
D=Fraction

def derive(inputs,decode):
 raw={}
 for key,relation in [('orders','ORDERS.ORDERS'),('shipments','ORDERS.SHIPMENTS'),('locations','INVENTORY.WAREHOUSE_LOCATIONS')]:
  item=inputs['relations'][relation];raw[key]=[dict(zip([c['name'].lower()for c in item['schema']],map(decode,row)))for row in item['rows']]
 locations=collections.Counter(r['warehouse_id'] for r in raw['locations']);hours=collections.defaultdict(lambda:collections.defaultdict(lambda:[0,0,D(0)]));orders=[];shipments=[]
 for r in raw['orders']:
  if r['warehouse_id'] is None or r['ordered_at'] is None:continue
  t=r['ordered_at'];day=t.date();h=t.hour;total=D(r['grand_total'])if r['grand_total']is not None else D(0);v=hours[r['warehouse_id'],day][h];v[0]+=1;v[2]+=total;orders.append({**r,'grand_total':total,'order_date':day,'order_hour':h,'day_of_week':(day.weekday()+1)%7})
 for r in raw['shipments']:
  if r['shipped_at'] is None:continue
  t=r['shipped_at'];day=t.date();h=t.hour;hours[r['warehouse_id'],day][h][1]+=1;shipments.append({**r,'shipment_date':day,'shipment_hour':h})
 hourly=[];daily=[];facts=[];bottlenecks=[];wh_hourly=collections.defaultdict(list)
 for (wid,day),hs in sorted(hours.items(),key=lambda item:str(item[0])):
  for h in range(24):
   oc,sc,ov=hs[h];r={'warehouse_id':wid,'volume_date':day,'volume_hour':h,'order_count':oc,'shipment_count':sc,'order_value':ov};hourly.append(r);wh_hourly[wid].append(r)
  oc=sum(x[0] for x in hs.values());sc=sum(x[1] for x in hs.values());ov=sum((x[2] for x in hs.values()),D(0));count=locations[wid];assert count>0,'Unspecified no-location capacity default requires separate review';cap=D(count)*D(1,20);daily.append({'warehouse_id':wid,'utilization_date':day,'daily_orders':oc,'daily_shipments':sc,'daily_order_value':ov,'location_count':count,'theoretical_daily_capacity':cap,'utilization_rate':D(oc)/cap})
 byday={ (r['warehouse_id'],r['utilization_date']):r for r in daily};roll=collections.defaultdict(list)
 for r in sorted(daily,key=lambda r:(str(r['warehouse_id']),r['utilization_date'])):
  wid=r['warehouse_id'];day=r['utilization_date'];hs=hours[wid,day];oc=r['daily_orders'];avg=D(oc)/24;mx=max(hs[h][0] for h in range(24));roll[wid].append(oc);f={'warehouse_id':wid,'capacity_date':day,'total_orders':oc,'total_shipments':r['daily_shipments'],'total_order_value':r['daily_order_value'],'utilization_rate':r['utilization_rate'],'avg_hourly_orders':avg,'max_hourly_orders':mx,'peak_load_factor':D(mx)/avg if avg else D(1),'capacity_headroom_pct':(1-r['utilization_rate'])*100,'rolling_7d_avg_orders':D(sum(roll[wid][-7:]))/len(roll[wid][-7:])};facts.append(f)
  if f['utilization_rate']>=D(3,4):bottlenecks.append({c:f[c] for c in ['warehouse_id','capacity_date','utilization_rate','total_orders','peak_load_factor']}|{'bottleneck_severity':'CRITICAL' if f['utilization_rate']>=D(19,20) else 'HIGH' if f['utilization_rate']>=D(17,20) else 'MODERATE'})
 # NTILE assigns larger buckets first; equal order_count rows may take any permutation.
 allowed={};bounds={};tie_facts=[]
 for wid,rows in wh_hourly.items():
  groups=collections.defaultdict(list)
  for r in rows:groups[r['order_count']].append(r)
  n=len(rows);q,rem=divmod(n,100);bucket_list=[b for b in range(1,101) for _ in range(q+(b<=rem))];assert len(bucket_list)==n;position=0;day_bounds=collections.defaultdict(lambda:[0,0]);boundary_slots=0
  for count,rs in sorted(groups.items()):
   buckets=bucket_list[position:position+len(rs)];position+=len(rs);allowed[wid,count]=dict(collections.Counter(buckets));peaks=sum(x>=90 for x in buckets);full=peaks==len(rs);partial=0<peaks<len(rs)
   for r in rs:day_bounds[r['volume_date']][0]+=int(full);day_bounds[r['volume_date']][1]+=int(partial)
   if partial:boundary_slots+=peaks
   tie_facts.append({'warehouse_id':wid,'order_count':count,'hours':len(rs),'allowed_bucket_multiset':dict(collections.Counter(buckets)),'peak_slots':peaks,'partial_peak_boundary_tie':partial})
  bounds[wid]={'daily':dict(day_bounds),'boundary_peak_slots':boundary_slots,'total_required_peak_slots':sum(x>=90 for x in bucket_list)}
 expected={'main_staging.stg_capacity__orders':orders,'main_staging.stg_capacity__shipments':shipments,'main_intermediate.int_capacity__hourly_volume':hourly,'main_intermediate.int_capacity__daily_utilization':daily,'main_marts.fct_warehouse_capacity':facts,'main_marts.rpt_capacity_bottlenecks':bottlenecks};keys={n: ['order_id'] if n.endswith('__orders') else ['shipment_id'] if n.endswith('__shipments') else ['warehouse_id','volume_date','volume_hour'] if n.endswith('hourly_volume') else ['warehouse_id','utilization_date'] if n.endswith('daily_utilization') else ['warehouse_id','capacity_date'] for n in expected}
 return expected,keys,{'allowed_bucket_multisets':allowed,'bounds':bounds,'tie_facts':tie_facts,'raw':raw,'hourly':hourly}
