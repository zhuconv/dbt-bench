"""Flat public-source reconstruction for product velocity; no submitted SQL/test import.
Category percentile instruction conflict and monthly stddev convention are explicit.
"""
from collections import defaultdict,Counter
from decimal import Decimal,ROUND_HALF_UP
import hashlib,json,datetime
D=lambda x:Decimal(str(x))
def rnd(v,n=2):return None if v is None else D(v).quantize(D(1).scaleb(-n),rounding=ROUND_HALF_UP)
def digest(v):return hashlib.sha256(json.dumps(v,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def std(v):
 m=sum(v,D(0))/len(v);return (sum(((x-m)**2 for x in v),D(0))/len(v)).sqrt()
def tile(rows,key):
 q,rem=divmod(len(rows),100);out={};at=0
 for k in range(1,101):
  size=q+(k<=rem)
  for r in sorted(rows,key=key)[at:at+size]:out[r['product_id']]=k
  at+=size
 return out
def derive_velocity(con):
 queries={'orders':'SELECT order_id,ordered_at,status FROM ORDERS.ORDERS ORDER BY order_id','lines':'SELECT order_line_id,order_id,product_id,product_name,quantity_ordered,quantity_returned,unit_price,discount_amount FROM ORDERS.ORDER_LINES ORDER BY order_line_id','products':'SELECT product_id,primary_category_id FROM PRODUCT.PRODUCTS ORDER BY product_id'}
 src={k:con.execute(q).fetchall() for k,q in queries.items()};assert len({r[0] for r in src['orders']})==len(src['orders']);assert len({r[0] for r in src['products']})==len(src['products'])
 orders={r[0]:r for r in src['orders'] if r[1] is not None and '2024-01-01'<=str(r[1])<'2025-01-01' and r[2] is not None and r[2].strip() not in ['CANCELLED','RETURNED','FAILED']};stage=[];byprod=defaultdict(list);byday=defaultdict(list);cats={r[0]:r[1] or 'UNCATEGORIZED' for r in src['products']}
 for lid,oid,p,name,q,returned,price,disc in src['lines']:
  if oid not in orders:continue
  assert q is not None and price is not None
  units=max(D(q)-D(returned or 0),D(0))
  if units<=0:continue
  day=orders[oid][1].date();gross=rnd(units*D(price));discount=rnd(D(disc or 0));net=rnd(gross-discount)
  r=dict(order_line_id=lid,order_id=oid,product_id=p,product_name=name,sale_date=day,units_sold=units,unit_price=price,gross_line_value=gross,discount_amount=discount,net_line_value=net);stage.append(r);byprod[p].append(r);byday[(p,day)].append(r)
 daily=[];months=defaultdict(list)
 for(p,day),ls in sorted(byday.items()):
  r=dict(product_id=p,sale_date=day,daily_units=sum((x['units_sold'] for x in ls),D(0)),daily_orders=len({x['order_id'] for x in ls}),daily_gross_revenue=sum((x['gross_line_value'] for x in ls),D(0)),daily_discount=sum((x['discount_amount'] for x in ls),D(0)),daily_net_revenue=sum((x['net_line_value'] for x in ls),D(0)));daily.append(r);months[(p,str(day)[:7])].append(r)
 monthly=[]
 for(p,month),ds in sorted(months.items()):
  monthly.append(dict(product_id=p,sale_month=month,monthly_units=sum((x['daily_units'] for x in ds),D(0)),monthly_orders=len({x['order_id'] for x in byprod[p] if str(x['sale_date'])[:7]==month}),monthly_net_revenue=sum((x['daily_net_revenue'] for x in ds),D(0))))
 metrics=[];half_boundary=[]
 for p,ls in sorted(byprod.items()):
  ds={r['sale_date']:r['daily_units'] for r in daily if r['product_id']==p};first=min(ds);last=max(ds);active=(last-first).days+1;mid=first+datetime.timedelta(days=active//2);names=Counter(x['product_name'] for x in ls);assert None not in names
  units=sum(ds.values(),D(0));gross=sum((x['gross_line_value'] for x in ls),D(0));discount=sum((x['discount_amount'] for x in ls),D(0));net=sum((x['net_line_value'] for x in ls),D(0));name=min(names,key=lambda n:(-names[n],n))
  metrics.append(dict(product_id=p,product_name=name,total_units_sold=units,total_orders=len({x['order_id'] for x in ls}),total_gross_revenue=gross,total_discount=discount,total_net_revenue=net,days_with_sales=len(ds),first_sale_date=first,last_sale_date=last,active_days=active,avg_daily_units=rnd(units/active),avg_daily_revenue=rnd(net/active),daily_units_std_dev=rnd(std(list(ds.values()))) if len(ds)>=2 else None,first_half_units=sum((v for day,v in ds.items() if day<mid),D(0)),second_half_units=sum((v for day,v in ds.items() if day>=mid),D(0))))
  if active%2:half_boundary.append({'product_id':p,'active_days':active,'floor_midpoint':str(mid),'units_on_floor_midpoint':str(ds.get(mid,D(0)))})
 revenue={r['product_id']:i+1 for i,r in enumerate(sorted(metrics,key=lambda r:(-r['total_net_revenue'],r['product_id'])))};vtile=tile(metrics,lambda r:(r['avg_daily_units'],r['product_id']));bycat=defaultdict(list)
 for r in metrics:bycat[cats.get(r['product_id'],'UNCATEGORIZED')].append(r)
 catrank={};catpercentile_desc={};catpercentile_asc={};catavg={}
 for c,rs in bycat.items():
  catavg[c]=rnd(sum((r['avg_daily_units'] for r in rs),D(0))/len(rs));catrank.update({r['product_id']:i+1 for i,r in enumerate(sorted(rs,key=lambda r:(-r['avg_daily_units'],r['product_id'])))});catpercentile_desc.update(tile(rs,lambda r:(-r['avg_daily_units'],r['product_id'])));catpercentile_asc.update(tile(rs,lambda r:(r['avg_daily_units'],r['product_id'])))
 mart=[];monthly_std_alternatives=[];seasonality_alternatives=[]
 for m in metrics:
  p=m['product_id'];u=m['total_units_sold'];gross=m['total_gross_revenue'];cat=cats.get(p,'UNCATEGORIZED');r=dict(m,category_id=cat)
  for col in ['total_units_sold','first_half_units','second_half_units']:r[col]=int(rnd(r[col],0))
  r.update(avg_unit_price=rnd(gross/u) if u else D(0),discount_rate=rnd(m['total_discount']*100/gross) if gross else D(0),sales_frequency=rnd(D(m['days_with_sales'])*100/m['active_days']),velocity_cv=rnd(m['daily_units_std_dev']*100/m['avg_daily_units']) if m['daily_units_std_dev'] is not None and m['avg_daily_units'] else None,velocity_change_ratio=rnd(m['second_half_units']/m['first_half_units']) if m['first_half_units'] and m['active_days']>=2 else None,revenue_rank=revenue[p],velocity_percentile=vtile[p])
  v=r['velocity_percentile'];cv=r['velocity_cv'];change=r['velocity_change_ratio'];r['velocity_tier']='Elite' if v>=95 else 'High' if v>=75 else 'Medium' if v>=40 else 'Low' if v>=15 else 'Minimal';r['consistency_tier']='Insufficient Data' if cv is None else 'Very Consistent' if cv<50 else 'Consistent' if cv<100 else 'Variable' if cv<200 else 'Highly Variable';r['velocity_trend']=('New Product' if m['active_days']<30 else 'Insufficient Data') if change is None else 'Accelerating' if change>D('1.25') else 'Growing' if change>D('1.05') else 'Stable' if change>=D('.95') else 'Slowing' if change>=D('.75') else 'Declining'
  ms=sorted([v for v in monthly if v['product_id']==p],key=lambda v:v['sale_month']);vals=[v['monthly_units'] for v in ms];n=len(vals);avg=sum(vals,D(0))/n;popcv=rnd(std(vals)*100/avg) if n>=2 and avg else None;samplecv=rnd(std(vals)*(D(n)/(n-1)).sqrt()*100/avg) if n>=2 and avg else None
  if popcv!=samplecv:monthly_std_alternatives.append({'product_id':p,'population':popcv,'sample':samplecv})
  season=rnd(max(vals)/avg) if n>=2 and avg else None;rounded_season=rnd(max(vals)/rnd(avg)) if n>=2 and rnd(avg) else None
  if season!=rounded_season:seasonality_alternatives.append({'product_id':p,'exact_mean':season,'rounded_mean':rounded_season})
  r.update(months_active=n,first_month_units=int(rnd(vals[0],0)) if n>=2 else None,last_month_units=int(rnd(vals[-1],0)) if n>=2 else None,month_velocity_change=rnd(vals[-1]-vals[0]) if n>=2 else None,rolling_3m_avg_units=rnd(sum(vals[-3:],D(0))/3) if n>=3 else None,recent_3m_units=rnd(sum(vals[-3:],D(0))) if n>=3 else None,recent_share_pct=rnd(sum(vals[-3:],D(0))*100/u) if n>=3 and u else None,monthly_cv=popcv,category_avg_daily_units=catavg[cat],vs_category_velocity=rnd(m['avg_daily_units']-catavg[cat]),category_velocity_rank=catrank[p],category_velocity_percentile=catpercentile_desc[p],seasonality_index=season)
  delta=r['vs_category_velocity'];r['category_velocity_tier']='Above' if delta>=D('.5') else 'Below' if delta<=D('-.5') else 'Near';mc=r['month_velocity_change'];r['monthly_trend']='New' if n<2 else 'Rapid Growth' if mc>=10 else 'Growth' if mc>=3 else 'Stable' if mc>-3 else 'Decline' if mc>-10 else 'Rapid Decline'
  r['momentum_score']={'Rapid Growth':40,'Growth':30,'Stable':20,'Decline':10,'Rapid Decline':0,'New':15}[r['monthly_trend']]+{'Accelerating':30,'Growing':20,'Stable':15,'Slowing':8,'Declining':2,'New Product':12,'Insufficient Data':10}[r['velocity_trend']];sc=r['momentum_score'];r['momentum_tier']='Hot' if sc>=60 else 'Warm' if sc>=45 else 'Cool' if sc>=25 else 'Cold';vc=popcv if popcv is not None else cv;r['volatility_band']='Insufficient' if vc is None else 'Highly Volatile' if vc>=200 else 'Volatile' if vc>=100 else 'Moderate' if vc>=50 else 'Stable'
  r['demand_pattern']='Breakout' if r['velocity_tier'] in ['Elite','High'] and r['monthly_trend'] in ['Rapid Growth','Growth'] and r['consistency_tier'] in ['Very Consistent','Consistent'] else 'Seasonal' if popcv is not None and popcv>=150 and n>=4 else 'Steady' if r['velocity_trend'] in ['Stable','Growing'] and r['monthly_trend']=='Stable' else 'Fading' if r['velocity_trend'] in ['Declining','Slowing'] and r['monthly_trend'] in ['Decline','Rapid Decline'] else 'New Entry' if r['monthly_trend']=='New' else 'Long Tail' if r['velocity_tier'] in ['Low','Minimal'] and r['consistency_tier'] in ['Variable','Highly Variable'] else 'Unclassified'
  score=int(rnd(D(35)*(1-D(revenue[p]-1)/(len(metrics)-1)),0)) if len(metrics)>1 else 35;score+={'Elite':25,'High':20,'Medium':15,'Low':8,'Minimal':3}[r['velocity_tier']];score+={'Very Consistent':20,'Consistent':16,'Variable':10,'Highly Variable':4,'Insufficient Data':10}[r['consistency_tier']];score+={'Rapid Growth':20,'Growth':16,'Stable':12,'Decline':6,'Rapid Decline':2,'New':10}[r['monthly_trend']];r['performance_score']=min(100,max(0,score));r['performance_grade']='A' if score>=80 else 'B' if score>=65 else 'C' if score>=50 else 'D' if score>=35 else 'F';assert len(r)==48;mart.append(r)
 results={'main.stg_order_lines__sales':stage,'main.int_product_daily_sales':daily,'main.int_product_monthly_sales':monthly,'main.int_product_sales_metrics':metrics,'main_velocity_analytics.product_sales_velocity':mart}
 meta={'sources':{k:{'query':queries[k],'rows':len(v),'sha256':digest(v)} for k,v in src.items()},'null_counts':{k:[sum(r[i] is None for r in rs) for i in range(len(rs[0]))] for k,rs in src.items()},'odd_active_day_midpoints':half_boundary,'executed_ambiguity_investigations':{'category_percentile_direction_conflicts':sum(catpercentile_asc[p]!=catpercentile_desc[p] for p in catpercentile_asc),'monthly_stddev_convention_differences':monthly_std_alternatives,'seasonality_intermediate_rounding_differences':seasonality_alternatives,'scope':'Instruction category DESC conflicts with higher percentile=faster note; both partitions actually computed. Monthly Stddev does not name population/sample; both actually computed. Seasonality exact vs rounded intermediate mean actually compared. Complete lawful baseline not asserted.'},'independent_columns':86,'scope':'Full 5 relation typed-value arithmetic under explicit population-monthly-stddev and DESC-category-percentile readings. Ambiguous fields and dependents are not certified as uniquely correct.'}
 return results,meta

def compare_velocity(expected,actual,scope=None):
 keys={'main.stg_order_lines__sales':['order_line_id'],'main.int_product_daily_sales':['product_id','sale_date'],'main.int_product_monthly_sales':['product_id','sale_month'],'main.int_product_sales_metrics':['product_id'],'main_velocity_analytics.product_sales_velocity':['product_id']};diff=[]
 for rel,rows in expected.items():
  if scope and rel not in scope:continue
  kk=keys[rel];em={tuple(str(r[k]) for k in kk):r for r in rows};obs=actual[rel]['rows'];am={tuple(str(r[k]) for k in kk):r for r in obs}
  if len(am)!=len(obs):diff.append({'relation':rel,'kind':'duplicate_keys'})
  for key in sorted(set(em)|set(am)):
   if key not in em or key not in am:diff.append({'relation':rel,'key':key,'kind':'extra' if key not in em else 'missing'});continue
   for col,v in em[key].items():
    if scope and scope[rel] and col not in scope[rel]:continue
    a=am[key][col]
    same=(a is not None and abs(D(a)-v)<D('.000001')) if isinstance(v,Decimal) else str(a)==str(v) if isinstance(v,datetime.date) else a==v
    if not same:diff.append({'relation':rel,'key':key,'column':col,'expected':v,'actual':a})
 return diff
