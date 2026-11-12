"""Independent Python derivation from ten explicitly allowed initial staging relations.
No native verifier, expected JSON or reference SQL is imported. No mutation score
is used to decide correctness. Null/empty aggregate representation is stated.
"""
from collections import defaultdict,Counter
from datetime import datetime,date,timedelta
from decimal import Decimal,ROUND_HALF_UP
import json

def decode(v):
 if isinstance(v,dict) and '$type' in v:
  kind=v['$type'].lower();value=v['value']
  if kind=='decimal':return Decimal(value)
  if kind=='float':return float(value)
  if kind=='datetime':return datetime.fromisoformat(value)
  if kind=='date':return date.fromisoformat(value)
  raise ValueError(kind)
 if isinstance(v,list):return [decode(x) for x in v]
 if isinstance(v,dict):return {k:decode(x) for k,x in v.items()}
 return v

def ts(v):
 if v is None:return None
 if isinstance(v,datetime):return v
 if isinstance(v,date):return datetime.combine(v,datetime.min.time())
 try:return datetime.fromisoformat(str(v))
 except ValueError:return None

def num(v):return Decimal(str(v)) if v is not None else None

def aggregate(values):
 vals=[num(v) for v in values if v is not None];return sum(vals,Decimal(0)) if vals else None

def mean(values):
 vals=[num(v) for v in values if v is not None];return sum(vals,Decimal(0))/len(vals) if vals else None

def first(*v):return next((x for x in v if x is not None),None)

def indexed(rows,key):
 out={}
 for r in rows:
  if r[key] is None:continue
  assert r[key] not in out,('Nonunique dimension; must explicitly adjudicate multiplicity',key,r[key]);out[r[key]]=r
 return out

def derive(raw,schemas):
 tables={k:[{a.lower():decode(b) for a,b in row.items()} for row in value['rows']] for k,value in raw['tables'].items()}
 reviews=tables['stg_product__product_reviews'];orders=indexed(tables['stg_orders__orders'],'order_id');lines=tables['stg_orders__order_lines'];lines_byid=indexed(lines,'order_line_id');fraud=indexed(tables['stg_orders__order_fraud_scores'],'order_id');customers=indexed(tables['stg_customer__customers'],'customer_id');ratings=indexed(tables['stg_product_ratings_summary'],'product_id')
 pvelocity=Counter();cvelocity=Counter()
 for r in reviews:
  r['review_ts']=first(ts(r['submitted_at']),ts(r['created_at']));r['review_date']=r['review_ts'].date() if r['review_ts'] else None
  pvelocity[(r['product_id'],r['review_date'])]+=1;cvelocity[(r['customer_id'],r['review_date'])]+=1
 returns=defaultdict(list);rlines=defaultdict(list);coupons=defaultdict(list)
 for r in tables['stg_orders__returns']:returns[r['order_id']].append(r)
 for r in tables['stg_orders__return_lines']:
  line=lines_byid.get(r['order_line_id'],{});rlines[(line.get('order_id'),line.get('product_id'))].append(r)
 for r in tables['stg_coupon_usage']:coupons[r['order_id']].append(r)
 review_products={r['product_id'] for r in reviews if r['product_id'] is not None};events=defaultdict(list);unparseable=[]
 for e in tables['stg_events']:
  if e['product_id'] not in review_products:continue
  event_ts=first(ts(e['event_timestamp']),ts(e['created_at']))
  if event_ts is None:unparseable.append(e['event_id'])
  else:events[e['product_id']].append(event_ts.date())
 assert not unparseable,('Relevant event timestamps unresolved',unparseable[:5])
 order_fields=['channel_id','order_source','order_type','payment_status','fulfillment_status','ordered_at','shipped_at','delivered_at','cancelled_at','fraud_score','fraud_check_status']
 linefields={'quantity_ordered':'quantity_ordered','line_quantity_returned':'quantity_returned','line_unit_price':'unit_price','line_discount_amount':'discount_amount','line_tax_amount':'tax_amount','line_total':'line_total','line_status':'status'}
 customerfields=['email_verified','phone_verified','churn_risk_tier','segment_ml','first_order_date','last_order_date','total_orders','total_lifetime_value','current_tier_id']
 ratingfields={'product_total_reviews':'total_reviews','product_average_rating':'average_rating','recommend_percentage':'recommend_percentage','product_last_review_date':'last_review_date',**{f'rating_{i}_count':f'rating_{i}_count' for i in range(1,6)}}
 enriched=[];matching=[];uncertain=[];branch=Counter()
 for r in reviews:
  candidates=[]
  for line in lines:
   order=orders.get(line['order_id'],{})
   exact=r['order_id'] is not None and r['order_id']==line['order_id'] and (r['product_id'] is None or r['product_id']==line['product_id'])
   fallback=r['order_id'] is None and r['customer_id'] is not None and r['customer_id']==order.get('customer_id') and r['product_id'] is not None and r['product_id']==line['product_id']
   od=ts(order.get('ordered_at'))
   if (exact or fallback) and (r['review_ts'] is None or od is not None and od<=r['review_ts']):
    rank=(0 if exact else 1,od is None,-(od-datetime(1970,1,1)).total_seconds() if od else 0,line['line_number'] is None,line['line_number'] or 0)
    candidates.append((rank,line,order))
  candidates.sort(key=lambda x:x[0])
  if len(candidates)>1 and candidates[0][0]==candidates[1][0]:uncertain.append({'review_id':r['review_id'],'kind':'equal_rank_tie','lines':[x[1]['order_line_id'] for x in candidates if x[0]==candidates[0][0]]})
  chosen=candidates[0] if candidates else None;bl=chosen[1] if chosen else {};bo=chosen[2] if chosen else {};fallback_order=orders.get(r['order_id'],{})
  branch['matched_line']+=bool(chosen);branch['order_id_present']+=r['order_id'] is not None;branch['multiple_candidates']+=len(candidates)>1
  matching.append({'review_id':r['review_id'],'candidates':len(candidates),'chosen_line':bl.get('order_line_id')})
  row={col:None for col in schemas['int_reviews__enriched']}
  for col in row:
   if col in r:row[col]=r[col]
  row['review_status']=r['status'];row['order_id']=first(bl.get('order_id'),r['order_id']);row['order_line_id']=bl.get('order_line_id');row['matched_customer_id']=first(bo.get('customer_id'),r['customer_id'])
  for col in order_fields:row[col]=first(bo.get(col),fallback_order.get(col))
  row['order_status']=first(bo.get('status'),fallback_order.get('status'))
  for dst,src in linefields.items():row[dst]=bl.get(src)
  fid=row['order_id'];f=fraud.get(fid,{}) if fid is not None else {}
  for dst,src in {'fraud_score_value':'score','fraud_risk_level':'risk_level','fraud_provider':'provider','fraud_reviewed_by':'reviewed_by','fraud_reviewed_at':'reviewed_at'}.items():row[dst]=f.get(src)
  rr=returns.get(fid,[]) if fid is not None else [];lr=rlines.get((fid,r['product_id']),[]) if fid is not None and r['product_id'] is not None else [];cu=coupons.get(fid,[]) if fid is not None else []
  branch['with_return_group']+=bool(rr);branch['with_return_line_group']+=bool(lr);branch['with_coupon_group']+=bool(cu);branch['with_fraud']+=bool(f)
  row['return_count']=len({x['return_id'] for x in rr if x['return_id'] is not None}) if rr else None;row['total_refund_amount']=aggregate(x['refund_amount'] for x in rr)
  if rr:
   statuses={x['status'] for x in rr if x['status'] is not None}
   if len(statuses)>1:uncertain.append({'review_id':r['review_id'],'kind':'latest_return_status_policy','statuses':sorted(statuses)})
   row['latest_return_status']=next(iter(statuses)) if len(statuses)==1 else None
  row['product_return_qty']=aggregate(x['quantity_returned'] for x in lr);row['product_return_line_count']=len({x['return_line_id'] for x in lr if x['return_line_id'] is not None}) if lr else None;row['product_return_refund']=aggregate(x['refund_amount'] for x in lr)
  row['coupon_redemption_count']=len({x['redemption_id'] for x in cu if x['redemption_id'] is not None}) if cu else None
  discounts=[]
  for x in cu:
   try:discounts.append(float(x['discount_amount']) if x['discount_amount'] is not None else None)
   except (ValueError,TypeError):discounts.append(None)
  row['coupon_discount_total']=aggregate(discounts)
  customer=customers.get(r['customer_id'],{});rating=ratings.get(r['product_id'],{})
  for col in customerfields:row[col]=customer.get(col)
  row['customer_status']=customer.get('status')
  for dst,src in ratingfields.items():row[dst]=rating.get(src)
  ev=[d for d in events.get(r['product_id'],[]) if r['review_date'] is not None and r['review_date']-timedelta(days=7)<=d<=r['review_date']]
  row['product_event_7d_count']=len(ev) if ev else None
  row['product_review_count_day']=pvelocity[(r['product_id'],r['review_date'])] if r['product_id'] is not None and r['review_date'] is not None else None
  row['customer_review_count_day']=cvelocity[(r['customer_id'],r['review_date'])] if r['customer_id'] is not None and r['review_date'] is not None else None
  enriched.append(row)
 facts=[]
 for base in enriched:
  r=dict(base);r['review_text_length']=len(r['review_text']) if r['review_text'] is not None else None
  start,end=ts(r['ordered_at']),ts(r['review_ts']);moderated=ts(r['moderated_at'])
  r['time_to_review_days']=(end.date()-start.date()).days if start and end else None
  r['moderation_latency_hours']=int((moderated.replace(minute=0,second=0,microsecond=0)-end.replace(minute=0,second=0,microsecond=0)).total_seconds()//3600) if moderated and end else None
  r['rating_delta']=num(r['rating'])-num(r['product_average_rating']) if r['rating'] is not None and r['product_average_rating'] is not None else None
  r.update(rating_outlier_flag=r['rating_delta'] is not None and abs(r['rating_delta'])>=2,coupon_used_flag=(r['coupon_redemption_count'] or 0)>0,return_flag=(r['product_return_qty'] or 0)>0 or (r['return_count'] or 0)>0,fraud_high_flag=r['fraud_risk_level'] in ['HIGH','CRITICAL'] or r['fraud_check_status']=='FAIL',unverified_purchase_flag=r['is_verified_purchase'] is not True,customer_velocity_flag=(r['customer_review_count_day'] or 0)>=3,product_velocity_flag=(r['product_review_count_day'] or 0)>=20,low_content_flag=r['review_text'] is None or len(r['review_text'])<20)
  penalties={'unverified_purchase_flag':30,'fraud_high_flag':20,'coupon_used_flag':10,'return_flag':10,'low_content_flag':5,'rating_outlier_flag':5,'customer_velocity_flag':5,'product_velocity_flag':5}
  score=sum(v for k,v in penalties.items() if r[k])+5*(r['email_verified'] is False)+5*(r['phone_verified'] is False)+5*(r['time_to_review_days'] is not None and r['time_to_review_days']<1)+5*(r['time_to_review_days'] is not None and r['time_to_review_days']>365)
  r['score_deductions']=score;r['trust_score']=max(0,100-score);r['risk_bucket']='HIGH' if r['trust_score']<50 else 'MEDIUM' if r['trust_score']<80 else 'LOW';r['queue_priority']='P1' if r['fraud_high_flag'] or r['unverified_purchase_flag'] or r['coupon_used_flag'] else 'P2' if r['rating_outlier_flag'] or r['customer_velocity_flag'] or r['product_velocity_flag'] else 'P3';facts.append(r)
 groups=defaultdict(list)
 for r in facts:
  month=datetime(r['review_date'].year,r['review_date'].month,1) if r['review_date'] else None
  groups[(month,r['channel_id'],r['review_source'],r['fraud_risk_level'],r['risk_bucket'],first(r['rejection_reason'],'NONE'))].append(r)
 kpis=[]
 for key,rows in groups.items():
  r=dict(zip(['review_month','channel_id','review_source','fraud_risk_level','risk_bucket','rejection_reason'],key));n=len(rows);verified=sum(x['is_verified_purchase'] is True for x in rows);r.update(review_count=n,verified_review_count=verified,verified_purchase_share=(Decimal(100)*verified/n).quantize(Decimal('.01'),rounding=ROUND_HALF_UP),avg_time_to_review_days=mean(x['time_to_review_days'] for x in rows),avg_moderation_latency_hours=mean(x['moderation_latency_hours'] for x in rows),avg_rating_delta=mean(abs(x['rating_delta']) if x['rating_delta'] is not None else None for x in rows),avg_trust_score=mean(x['trust_score'] for x in rows))
  for dst,flag in {'rating_outlier_count':'rating_outlier_flag','product_velocity_flag_count':'product_velocity_flag','customer_velocity_flag_count':'customer_velocity_flag','reviews_with_returns':'return_flag','reviews_with_coupons':'coupon_used_flag'}.items():r[dst]=sum(x[flag] for x in rows)
  kpis.append(r)
 output={'int_reviews__enriched':enriched,'fct_review_moderation_risk':facts,'rpt_review_moderation_kpis':kpis}
 for name,rows in output.items():assert all(set(x)==set(schemas[name]) for x in rows),(name,set(rows[0])-set(schemas[name]),set(schemas[name])-set(rows[0]))
 return {'outputs':output,'branch_counts':dict(branch),'unresolved':uncertain,'matching':matching,'event_product_overlap':sum(len(x) for x in events.values()),'policies':['Absent optional joined aggregate groups retain SQL NULL; no extra zero-fill requirement is asserted. Flags explicitly handle these as zero/false.','Integer fields/IDs/bools/dates compared exactly. Numeric fields use Decimal arithmetic; per-numeric-field absolute1e-8 comparison only for serialization/average float accumulation, recorded individually.','Latest return status with multiple differing statuses and equal-ranked line ties remain unresolved rather than copying reference MAX or arbitrary order.']}
