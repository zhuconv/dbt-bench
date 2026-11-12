"""Independent Python fulfillment formulas; no reference/native checker imports."""
import collections,datetime,decimal,math,statistics
D=decimal.Decimal
QUERIES={
 'orders':'SELECT order_id,customer_id,ordered_at,shipped_at,delivered_at,grand_total,status,test_order_flag,is_first_order,warehouse_id FROM main.stg_orders__orders',
 'shipments':'SELECT shipment_id,order_id,warehouse_id,carrier_id,shipping_method_id,shipping_cost,weight FROM main.stg_orders__shipments',
 'methods':'SELECT shipping_method_id,shipping_method_code,shipping_method_name,carrier_id,estimated_days_min,estimated_days_max,is_express FROM main.stg_wms__ship_methods',
 'warehouses':'SELECT warehouse_id,warehouse_code,warehouse_name,warehouse_type,state_province,country_code FROM main.stg_wms__warehouses',
 'carriers':'SELECT carrier_id,carrier_code,carrier_name,carrier_type FROM main.stg_reference__carriers'}
KEYS={'int_shipment_times':['shipment_id'],'fulfillment_sla':['fulfillment_month','shipping_method_id','warehouse_id'],'carrier_performance':['carrier_id'],'warehouse_performance':['warehouse_id'],'fulfillment_summary':['carrier_name','service_tier']}
ROUNDING_OVERRIDES = {}
ROUNDING_TIES = set()
def rounded(x,n=2):
 if x is None:return None
 value=D(str(x));key=(str(value.normalize()),n);quantum=D(1).scaleb(-n)
 up=value.quantize(quantum,rounding=decimal.ROUND_HALF_UP);even=value.quantize(quantum,rounding=decimal.ROUND_HALF_EVEN)
 if up!=even:ROUNDING_TIES.add(key)
 return even if ROUNDING_OVERRIDES.get(key)=="half_even" else up
def avg(xs):return sum((D(str(x)) for x in xs),D(0))/len(xs) if xs else None
def pct(n,d):return rounded(D(n)*100/d,1) if d else None
def truth(x):return str(x).upper() in ('1','TRUE','T','Y','YES')
def groups(rows,cols):
 out=collections.defaultdict(list)
 for r in rows:out[tuple(r[c] for c in cols)].append(r)
 return out

def quantile(xs,p):
 a=sorted(xs);i=D(len(a)-1)*D(str(p));lo=int(i);frac=i-lo;return D(a[lo])*(1-frac)+D(a[min(lo+1,len(a)-1)])*frac

def build(raw):
 idx={key:{r[idcol]:r for r in raw[key] if r[idcol] is not None} for key,idcol in [('orders','order_id'),('methods','shipping_method_id'),('warehouses','warehouse_id'),('carriers','carrier_id')]}
 rows=[];rejections=collections.Counter();fallback_candidates=[]
 for s in raw['shipments']:
  o=idx['orders'].get(s['order_id']);m=idx['methods'].get(s['shipping_method_id']);w=idx['warehouses'].get(s['warehouse_id']);c=idx['carriers'].get(m['carrier_id']) if m else None
  if o and s['warehouse_id'] is None and o['warehouse_id'] in idx['warehouses']:fallback_candidates.append(s['shipment_id'])
  if not o or not m or not w or not c or s['carrier_id'] is None:rejections['missing_required_fk_or_join']+=1;continue
  if m['estimated_days_min'] is None or m['estimated_days_max'] is None:rejections['missing_estimates']+=1;continue
  if o['status']!='DELIVERED' or truth(o['test_order_flag']):rejections['status_or_test']+=1;continue
  ts=[o[k] for k in ['ordered_at','shipped_at','delivered_at']]
  if any(t is None for t in ts):rejections['null_timestamps']+=1;continue
  a,b,z=[datetime.datetime.fromisoformat(t) if isinstance(t,str) else t for t in ts]
  if not datetime.date(2023,1,1)<=a.date()<datetime.date(2024,12,1):rejections['period']+=1;continue
  if not a<b<z:rejections['nonpositive_intervals']+=1;continue
  p,t,total=(b.date()-a.date()).days,(z.date()-b.date()).days,(z.date()-a.date()).days
  status='Early' if t<m['estimated_days_min'] else 'On-Time' if t<=m['estimated_days_max'] else 'Late'
  severity='None' if status!='Late' else 'Minor' if t-m['estimated_days_max']<=2 else 'Major' if t-m['estimated_days_max']<=7 else 'Severe'
  wd,cd=int(p>2),int(t>m['estimated_days_max']);assert o['grand_total'] is not None,'NULL grand_total tier is unspecified'
  r={k:o[k] for k in ['order_id','customer_id','grand_total','is_first_order']};r.update(ordered_at=a,shipped_at=b,delivered_at=z)
  r.update({k:s[k] for k in ['shipment_id','shipping_method_id','warehouse_id','shipping_cost','weight']});r.update({k:m[k] for k in ['shipping_method_code','shipping_method_name','estimated_days_min','estimated_days_max']});r.update({k:v for k,v in w.items() if k!='warehouse_id'});r.update(c)
  r.update(is_express=int(truth(m['is_express'])),processing_days=p,transit_days=t,total_days=total,value_tier='Budget' if o['grand_total']<50 else 'Standard' if o['grand_total']<150 else 'Premium',on_time_status=status,breach_severity=severity,warehouse_delay=wd,carrier_delay=cd,delay_attribution={(0,0):'None',(1,0):'Warehouse Only',(0,1):'Carrier Only',(1,1):'Both'}[wd,cd]);rows.append(r)
 def compliant(r):return r['on_time_status']!='Late'
 def count(rs,p):return sum(bool(p(r)) for r in rs)
 def tiers(rs):
  r={}
  for tier in ['Budget','Standard','Premium']:
   sub=[x for x in rs if x['value_tier']==tier];r[tier.lower()+'_shipments']=len(sub);r[tier.lower()+'_sla_rate']=pct(count(sub,compliant),len(sub))
  return r
 def base(rs):
  r={'total_shipments':len(rs),'total_order_value':rounded(sum((x['grand_total'] for x in rs),D(0)))}
  for interval in ['processing','transit','total']:r['avg_'+interval+'_days']=rounded(avg([x[interval+'_days'] for x in rs]))
  return r
 monthly=[]
 for r in rows:r['_month']=r['ordered_at'].strftime('%Y-%m')
 for k,rs in groups(rows,['_month','shipping_method_id','warehouse_id']).items():
  r={c:rs[0][c] for c in ['shipping_method_id','shipping_method_name','warehouse_id','warehouse_name','carrier_name','is_express','estimated_days_min','estimated_days_max']};r['fulfillment_month']=k[0];r.update(base(rs));r.update(tiers(rs));days=[x['total_days'] for x in rs]
  for q in [50,75,90,95]:r[f'p{q}_total_days']=rounded(quantile(days,q/100))
  r.update(min_total_days=min(days),max_total_days=max(days),is_peak_month=int(int(k[0][-2:]) in [11,12]))
  for stat,label in [('Early','early'),('On-Time','ontime'),('Late','late')]:r[label+'_count']=count(rs,lambda x:x['on_time_status']==stat);r[label+'_pct']=pct(r[label+'_count'],len(rs))
  r['sla_compliance_rate']=pct(count(rs,compliant),len(rs))
  for severity in ['Minor','Major','Severe']:r[severity.lower()+'_breach_count']=count(rs,lambda x:x['breach_severity']==severity)
  r['avg_days_late']=rounded(avg([x['transit_days']-x['estimated_days_max'] for x in rs if not compliant(x)]))
  for typ in ['warehouse','carrier']:r[typ+'_delay_count']=count(rs,lambda x:x[typ+'_delay']);r[typ+'_delay_pct']=pct(r[typ+'_delay_count'],len(rs))
  r['both_delay_count']=count(rs,lambda x:x['warehouse_delay'] and x['carrier_delay']);monthly.append(r)
 for _,rs in groups(monthly,['shipping_method_id','warehouse_id']).items():
  prev=None
  for r in sorted(rs,key=lambda r:r['fulfillment_month']):r['month_over_month_volume_change_pct']=None if prev is None else rounded(D(r['total_shipments']-prev)*100/prev,1);prev=r['total_shipments']
 monthly.sort(key=lambda r:(r['fulfillment_month'],r['warehouse_name'],r['shipping_method_name']))
 performances={}
 for typ in ['carrier','warehouse']:
  perf=[];idcol=typ+'_id';time='transit' if typ=='carrier' else 'processing';stdcol='transit_time_std_dev' if typ=='carrier' else 'processing_time_std_dev';speed='speed_score' if typ=='carrier' else 'efficiency_score'
  dims=['carrier_id','carrier_code','carrier_name','carrier_type'] if typ=='carrier' else ['warehouse_id','warehouse_code','warehouse_name','warehouse_type','state_province','country_code']
  for _,rs in groups(rows,[idcol]).items():
   r={c:rs[0][c] for c in dims};r.update(base(rs));r.update(tiers(rs));r['unique_orders']=len({x['order_id'] for x in rs});r['avg_shipment_value']=rounded(r['total_order_value']/len(rs));days=[x[time+'_days'] for x in rs]
   for q in [50,95]:r[f'p{q}_{time}_days']=rounded(quantile(days,q/100))
   r[stdcol]=rounded(statistics.stdev(days)) if len(days)>1 else None
   for col,pred in [('sla_compliance_rate',compliant),('early_delivery_rate',lambda x:x['on_time_status']=='Early'),('severe_breach_rate',lambda x:x['breach_severity']=='Severe')]:r[col]=pct(count(rs,pred),len(rs))
   r['premium_to_budget_sla_diff']=None if r['premium_sla_rate'] is None or r['budget_sla_rate'] is None else rounded(r['premium_sla_rate']-r['budget_sla_rate'],1)
   if typ=='carrier':
    for label,truthv in [('express',1),('standard_shipping',0)]:sub=[x for x in rs if x['is_express']==truthv];r[label+'_shipments']=len(sub);r[label+'_sla_rate']=pct(count(sub,compliant),len(sub))
   else:
    r['warehouse_caused_delay_rate']=pct(count(rs,lambda x:x['warehouse_delay']),len(rs));r['distinct_carriers']=len({x['carrier_id'] for x in rs});cc=collections.Counter(x['carrier_name'] for x in rs);primary=min(cc,key=lambda k:(-cc[k],k));r['primary_carrier_name']=primary;r['primary_carrier_pct']=pct(cc[primary],len(rs))
   r[speed]=rounded(max(0,min(100,100-r['avg_total_days' if typ=='carrier' else 'avg_processing_days']*(5 if typ=='carrier' else 20))),1);r['reliability_score']=r['sla_compliance_rate'];r['consistency_score']=rounded(max(0,min(100,100-(r[stdcol] or 0)*(10 if typ=='carrier' else 20))),1)
   weights=['.3','.5','.2'] if typ=='carrier' else ['.4','.4','.2'];r['composite_score']=rounded(sum(r[c]*D(w) for c,w in zip([speed,'reliability_score','consistency_score'],weights)),1);r['volume_weighted_score']=rounded(r['composite_score']*(1+D(str(math.log(len(rs))))/10),1);score=r['composite_score'];r['performance_tier']='Elite' if score>=85 else 'Strong' if score>=70 else 'Average' if score>=55 else 'Underperforming'
   r['first_shipment_date']=min(x['ordered_at'].date() for x in rs);r['last_shipment_date']=max(x['ordered_at'].date() for x in rs);months=groups(rs,['_month']);r['active_months']=len(months);r['recent_trend']=None
   if len(months)>=6:
    ordered=sorted(months,reverse=True);recent=[x for k in ordered[:3] for x in months[k]];prior=[x for k in ordered[3:6] for x in months[k]];delta=D(count(recent,compliant))*100/len(recent)-D(count(prior,compliant))*100/len(prior);r['recent_trend']='Improving' if delta>2 else 'Declining' if delta<-2 else 'Stable'
   perf.append(r)
  perf.sort(key=lambda r:(-r['composite_score'],r[idcol]));bytype=collections.Counter()
  for i,r in enumerate(perf,1):r[typ+'_rank']=i;bytype[r[typ+'_type']]+=1;r['rank_within_type']=bytype[r[typ+'_type']]
  performances[typ+'_performance']=perf
 summary=[];carriercounts=collections.Counter(r['carrier_name'] for r in rows)
 for r in rows:r['_tier']='Express' if r['is_express'] else 'Standard'
 for (name,tier),rs in groups(rows,['carrier_name','_tier']).items():
  b=base(rs);r={k:b[k] for k in ['total_shipments','total_order_value','avg_total_days']};r.update(carrier_name=name,service_tier=tier,sla_compliance_rate=pct(count(rs,compliant),len(rs)),severe_breach_pct=pct(count(rs,lambda x:x['breach_severity']=='Severe'),len(rs)),warehouse_delay_pct=pct(count(rs,lambda x:x['warehouse_delay']),len(rs)),carrier_delay_pct=pct(count(rs,lambda x:x['carrier_delay']),len(rs)),pct_of_carrier_volume=pct(len(rs),carriercounts[name]),pct_of_total_volume=pct(len(rs),len(rows)))
  r['value_tier_distribution']=', '.join(f'{t}: {pct(count(rs,lambda x:x["value_tier"]==t),len(rs)):.1f}%' for t in ['Budget','Standard','Premium']);summary.append(r)
 summary.sort(key=lambda r:(r['carrier_name'],r['service_tier']))
 for r in rows:r.pop('_month');r.pop('_tier')
 return {'int_shipment_times':rows,'fulfillment_sla':monthly,**performances,'fulfillment_summary':summary},{'raw_counts':{k:len(v) for k,v in raw.items()},'excluded_shipments':dict(rejections),'eligible_shipments':len(rows),'candidate_order_warehouse_fallback_shipments':fallback_candidates,'interpretations':['Month-over-month uses prior observed month in each partition. Trend compares last six active shipment months, consistently with count of active months.','Sample standard deviation, matching usual SQL STDDEV semantics. Score formulas use the prescribed rounded displayed metric inputs. Shipment/header source lineage and nonnull shipment FKs checked separately.']}


def build_variants(raw, max_ties=8):
 """Enumerate exact mathematical midpoint conventions, with full downstream recomputation.

 No difference outside actual exact Decimal half ties is tolerated. Identifiers,
 NULLs and all non-tied numeric cells remain exact. Bound prevents explosion;
 exceeding it is an audit evidence limitation, never a candidate failure.
 """
 import itertools
 global ROUNDING_OVERRIDES, ROUNDING_TIES
 ROUNDING_OVERRIDES={};ROUNDING_TIES=set();baseline,facts=build(raw)
 # Discover downstream ties introduced by alternative upstream rounding.
 known=set(ROUNDING_TIES)
 while True:
  if len(known)>max_ties:raise ValueError('Too many independent rounding ties for bounded current-input audit')
  keys=sorted(known);variants=[]
  for choices in itertools.product([False,True],repeat=len(keys)):
   ROUNDING_OVERRIDES={k:'half_even'for k,v in zip(keys,choices)if v};rows,_=build(raw);variants.append(rows)
  expanded=known|ROUNDING_TIES
  if expanded==known:break
  known=expanded
 ROUNDING_OVERRIDES={}
 facts['rounding_policy']={'kind':'nearest_explicit_midpoint_alternatives','tie_inputs':[list(k)for k in sorted(known)],'variants':len(variants),'arbitrary_absolute_tolerance':0,'scope':'Each exact midpoint rounding choice is recomputed through all downstream scores/ranks. Choices at the same exact numeric value and precision share a deterministic convention; current observed four tied cells are distinct inputs.'}
 return variants,facts
