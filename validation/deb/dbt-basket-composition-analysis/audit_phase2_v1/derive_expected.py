"""Flat raw input reconstruction for all six basket models, independent of SQL outputs/checker."""
from collections import defaultdict,Counter
from decimal import Decimal,ROUND_HALF_UP
import hashlib,json,math
D=lambda x:Decimal(str(x))
def rnd(x,n=2):return None if x is None else D(x).quantize(D(1).scaleb(-n),rounding=ROUND_HALF_UP)
def total(v):return sum(v,D(0))
def avg(v):return total(v)/len(v)
def ratio(a,b):return None if b is None or b==0 else D(a)/D(b)
def cat(n):return 'Single Item' if n==1 else 'Small Basket' if n==2 else 'Medium Basket' if n<=4 else 'Large Basket'
CATS=['Single Item','Small Basket','Medium Basket','Large Basket'];SCHEMA='main_basket_analytics.'
def digest(v):return hashlib.sha256(json.dumps(v,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def derive_basket(con):
 queries={'orders':'SELECT order_id,customer_id,ordered_at,grand_total,status FROM ORDERS.ORDERS ORDER BY order_id','lines':'SELECT ORDER_LINE_ID,ORDER_ID,PRODUCT_ID,PRODUCT_NAME,QUANTITY_ORDERED,UNIT_PRICE,DISCOUNT_AMOUNT,LINE_TOTAL FROM ORDERS.ORDER_LINES ORDER BY ORDER_LINE_ID'}
 src={k:con.execute(q).fetchall() for k,q in queries.items()};assert len(src['orders'])==len({r[0] for r in src['orders']})
 orders={r[0]:r for r in src['orders'] if '2024-01-01'<=str(r[2])<'2025-01-01' and r[4] is not None and r[4].strip() not in ['CANCELLED','RETURNED','FAILED']};linegroups=defaultdict(list)
 for r in src['lines']:
  if r[1] in orders:linegroups[r[1]].append(r)
 baskets=[]
 for oid,ls in sorted(linegroups.items()):
  o=orders[oid];date=o[2].date();n=len({r[2] for r in ls if r[2] is not None});assert n>0;money=[D(r[7]) for r in ls if r[7] is not None];baskets.append(dict(order_id=oid.strip(),customer_id=o[1].strip(),order_date=date,order_month=date.month,order_half='H1' if date.month<=6 else 'H2',order_quarter='Q'+str((date.month-1)//3+1),distinct_items=n,total_quantity=total(D(r[4]) for r in ls if r[4] is not None),basket_value=rnd(total(money)),total_discount=rnd(total(D(r[6] or 0) for r in ls)),avg_item_price=rnd(total(money)/n),max_line_value=rnd(max(money)),min_line_value=rnd(min(money)),price_range=rnd(max(money)-min(money))))
 bycust=defaultdict(list);bycat=defaultdict(list)
 for r in baskets:bycust[r['customer_id']].append(r);bycat[cat(r['distinct_items'])].append(r)
 customers=[];transitions=[];firstorders=[]
 for c,bs in sorted(bycust.items()):
  bs.sort(key=lambda r:(r['order_date'],r['order_id']));firstorders.append(bs[0]);h1=[r for r in bs if r['order_half']=='H1'];h2=[r for r in bs if r['order_half']=='H2'];single=sum(r['distinct_items']==1 for r in bs);n=len(bs)
  customers.append(dict(customer_id=c,total_orders=n,avg_basket_size=rnd(avg([r['distinct_items'] for r in bs])),avg_basket_value=rnd(avg([r['basket_value'] for r in bs])),total_items_purchased=rnd(total(r['total_quantity'] for r in bs)),total_spent=rnd(total(r['basket_value'] for r in bs)),max_basket_size=max(r['distinct_items'] for r in bs),max_basket_value=max(r['basket_value'] for r in bs),single_item_orders=single,multi_item_orders=n-single,multi_item_ratio=rnd(D(n-single)*100/n),avg_price_range=rnd(avg([r['price_range'] for r in bs])),h1_orders=len(h1),h2_orders=len(h2),h1_revenue=total(r['basket_value'] for r in h1),h2_revenue=total(r['basket_value'] for r in h2)))
  for a,b in zip(bs,bs[1:]):transitions.append({'from_category':cat(a['distinct_items']),'to_category':cat(b['distinct_items']),'days':(b['order_date']-a['order_date']).days,'customer':c,'from_order':a['order_id'],'to_order':b['order_id']})
 tg=defaultdict(list)
 for t in transitions:tg[(t['from_category'],t['to_category'])].append(t)
 transition_rows=[dict(from_category=a,to_category=b,transition_count=len(ts),transition_pct=rnd(D(len(ts))*100/len(transitions)),avg_days_between=rnd(avg([t['days'] for t in ts])),is_upgrade=CATS.index(b)>CATS.index(a),is_downgrade=CATS.index(b)<CATS.index(a)) for (a,b),ts in sorted(tg.items())]
 cohorts=defaultdict(list)
 for c,bs in bycust.items():cohorts[cat(bs[0]['distinct_items'])].append(c)
 cohort_rows=[]
 for category,cs in cohorts.items():
  bs=[r for c in cs for r in bycust[c]];nonfirst=[r for c in cs for r in bycust[c][1:]];ci=CATS.index(category);moves=Counter((CATS.index(cat(r['distinct_items']))>ci)-(CATS.index(cat(r['distinct_items']))<ci) for r in nonfirst)
  cohort_rows.append(dict(cohort_category=category,cohort_size=len(cs),total_cohort_orders=len(bs),total_cohort_revenue=total(r['basket_value'] for r in bs),avg_orders_per_customer=rnd(D(len(bs))/len(cs)),repeat_rate=rnd(D(sum(len(bycust[c])>=2 for c in cs))*100/len(cs)),upgrade_rate=rnd(ratio(moves[1]*100,len(nonfirst))),downgrade_rate=rnd(ratio(moves[-1]*100,len(nonfirst))),same_category_rate=rnd(ratio(moves[0]*100,len(nonfirst))),cohort_share_pct=rnd(D(len(cs))*100/len(bycust))))
 cohort_rows.sort(key=lambda r:-r['cohort_size'])
 activity={c:sum(t['from_category']==c or t['to_category']==c for t in transitions) for c in bycat};avgactivity=avg(list(activity.values()));mart=[]
 allrev=total(r['basket_value'] for r in baskets)
 for c,bs in bycat.items():
  n=len(bs);cs={r['customer_id'] for r in bs};rev=total(r['basket_value'] for r in bs);disc=total(r['total_discount'] for r in bs);h1=[r for r in bs if r['order_half']=='H1'];h2=[r for r in bs if r['order_half']=='H2'];h1r=total(r['basket_value'] for r in h1);h2r=total(r['basket_value'] for r in h2);av=rnd(rev/n);os=rnd(D(n)*100/len(baskets));rs=rnd(rev*100/allrev);reach=rnd(D(len(cs))*100/len(bycust));outgoing=[t for t in transitions if t['from_category']==c]
  first=rnd(D(sum(cat(r['distinct_items'])==c for r in firstorders))*100/n);repeat=rnd(D(sum(len(bycust[x])>=2 for x in cs))*100/len(cs));q=[sum(r['order_quarter']=='Q'+str(i) for r in bs) for i in range(1,5)]
  m=dict(basket_size_category=c,order_count=n,total_revenue=rev,avg_basket_value=av,avg_quantity_per_order=rnd(avg([r['total_quantity'] for r in bs])),total_customers=len(cs),avg_discount_per_order=rnd(disc/n),order_share_pct=os,revenue_share_pct=rs,customer_share_pct=reach,avg_item_price=rnd(avg([r['avg_item_price'] for r in bs])),revenue_per_customer=rnd(rev/len(cs)),size_index=rnd(rs/os),value_tier='Premium' if av>=500 else 'Standard' if av>=200 else 'Economy',discount_efficiency=rnd(rev/(disc+1)),avg_price_range=rnd(avg([r['price_range'] for r in bs])),h1_order_count=len(h1),h2_order_count=len(h2),h1_revenue=h1r,h2_revenue=h2r,order_growth_rate=rnd(ratio((len(h2)-len(h1))*100,len(h1))),revenue_growth_rate=rnd(ratio((h2r-h1r)*100,h1r)),quarter_order_volatility=rnd((sum((D(v)-D(n)/4)**2 for v in q)/4).sqrt()),avg_customer_orders=rnd(D(n)/len(cs)),first_order_pct=first,repeat_customer_pct=repeat,upgrade_rate=rnd(ratio(sum(CATS.index(t['to_category'])>CATS.index(c) for t in outgoing)*100,len(outgoing))),category_velocity_score=(40 if activity[c]>avgactivity else 20)+(30 if first>=40 else 20 if first>=25 else 10 if first>=10 else 5)+(30 if repeat>=70 else 22 if repeat>=50 else 15 if repeat>=30 else 8))
  mart.append(m)
 rev_ranks={v:i+1 for i,v in enumerate(sorted({r['revenue_share_pct'] for r in mart},reverse=True))};pop_ranks={v:i+1 for i,v in enumerate(sorted({r['order_count'] for r in mart},reverse=True))};total_rev_ranks={v:i+1 for i,v in enumerate(sorted({r['total_revenue'] for r in mart},reverse=True))}
 for r in mart:
  si=r['size_index'];reach=r['customer_share_pct'];vt=r['value_tier'];og=r['order_growth_rate'];rg=r['revenue_growth_rate']
  def points(v,pp,null):return null if v is None else pp[0] if v>50 else pp[1] if v>20 else pp[2] if v>0 else pp[3] if v==0 else pp[4] if v>=-20 else 0
  eff=max(5,min(100,max(0,40-10*rev_ranks[r['revenue_share_pct']])+(25 if si>=2 else 20 if si>=D('1.5') else 15 if si>=D('1.2') else 10 if si>=1 else 5)+(25 if reach>=60 else 20 if reach>=40 else 15 if reach>=25 else 10 if reach>=10 else 5)+{'Premium':20,'Standard':12,'Economy':5}[vt]))
  growth=max(5,min(100,points(og,[35,28,20,10,5],15)+points(rg,[30,24,18,9,4],12)+(20 if reach<10 else 15 if reach<25 else 10 if reach<40 else 5 if reach<60 else 2)+{'Economy':15,'Standard':10,'Premium':3}[vt]))
  et='High Performance' if eff>=75 else 'Good Performance' if eff>=55 else 'Average Performance' if eff>=35 else 'Needs Improvement';gt='High Potential' if growth>=70 else 'Moderate Potential' if growth>=50 else 'Low Potential' if growth>=30 else 'Saturated'
  r.update(popularity_rank=pop_ranks[r['order_count']],growth_trend=None if og is None else 'Accelerating' if og>20 else 'Growing' if og>0 else 'Stable' if og==0 else 'Declining' if og>=-20 else 'Contracting',basket_efficiency_score=eff,growth_potential_score=growth,efficiency_tier=et,growth_tier=gt,strategic_classification='Core Revenue Driver' if vt=='Premium' and reach>=25 else 'Growth Opportunity' if vt in ['Premium','Standard'] and reach<25 else 'Volume Leader' if reach>=50 and vt!='Premium' else 'Niche Segment',investment_priority='Star' if et=='High Performance' and gt in ['High Potential','Moderate Potential'] else 'Cash Cow' if et in ['High Performance','Good Performance'] and gt in ['Low Potential','Saturated'] else 'Question Mark' if et in ['Average Performance','Needs Improvement'] and gt in ['High Potential','Moderate Potential'] else 'Underperformer' if et=='Needs Improvement' and gt in ['Low Potential','Saturated'] else 'Stable Performer',category_momentum=None if og is None or rg is None else rnd(og*D('.4')+rg*D('.6')),rank_consistency=abs(pop_ranks[r['order_count']]-total_rev_ranks[r['total_revenue']]),composite_score=int(rnd(D(eff+growth)/2,0)))
 for r in mart:
  vs=rnd(r['basket_efficiency_score']-avg([v['basket_efficiency_score'] for v in mart]));r.update(efficiency_vs_avg=vs,relative_discount_rate=rnd(r['avg_discount_per_order']*100/avg([v['avg_discount_per_order'] for v in mart])),performance_quadrant='Rising Star' if vs>0 and r['growth_potential_score']>=60 else 'Established Leader' if vs>0 else 'High Potential' if r['growth_potential_score']>=60 else 'Needs Attention')
 mart.sort(key=lambda r:-r['order_count']);flow=[]
 for c in sorted(bycat):
  incoming=sum(t['to_category']==c and t['from_category']!=c for t in transitions);outgoing=sum(t['from_category']==c and t['to_category']!=c for t in transitions);retention=sum(t['from_category']==t['to_category']==c for t in transitions);net=incoming-outgoing
  flow.append(dict(category=c,total_incoming_transitions=incoming,total_outgoing_transitions=outgoing,net_transition_flow=net,retention_transitions=retention,inflow_rate=rnd(D(incoming)*100/len(transitions)),outflow_rate=rnd(D(outgoing)*100/len(transitions)),transition_balance='Net Gainer' if net>0 else 'Net Loser' if net<0 else 'Balanced'))
 results={SCHEMA+'stg_order_baskets':baskets,SCHEMA+'int_customer_basket_patterns':customers,SCHEMA+'int_basket_transitions':transition_rows,SCHEMA+'int_customer_cohorts':cohort_rows,SCHEMA+'basket_size_analysis':mart,SCHEMA+'transition_summary':flow}
 meta={'sources':{k:{'query':queries[k],'rows':len(v),'sha256':digest(v)} for k,v in src.items()},'source_null_counts':{k:[sum(r[i] is None for r in v) for i in range(len(v[0]))] for k,v in src.items()},'eligible_orders_with_lines':len(baskets),'eligible_customers':len(customers),'source_first_order_witnesses':[{k:r[k] for k in ['customer_id','order_id','order_date','distinct_items']} for r in firstorders],'source_transitions':transitions,'distinct_involved_transition_counts':activity,'mean_category_transition_activity':str(avgactivity),'no_native_expected_constants_used':True,'full_declared_column_counts':{k:len(v[0]) for k,v in results.items()}}
 return results,meta

def compare_basket(expected,actual,scope=None):
 keys={'stg_order_baskets':['order_id'],'int_customer_basket_patterns':['customer_id'],'int_basket_transitions':['from_category','to_category'],'int_customer_cohorts':['cohort_category'],'basket_size_analysis':['basket_size_category'],'transition_summary':['category']};differences=[]
 for rel,rows in expected.items():
  if scope and rel not in scope:continue
  kk=keys[rel.split('.')[-1]];em={tuple(r[k] for k in kk):r for r in rows};obs=actual[rel]['rows'];am={tuple(r[k] for k in kk):r for r in obs}
  if len(am)!=len(obs):differences.append({'relation':rel,'kind':'duplicate_keys'})
  for key in sorted(set(em)|set(am)):
   if key not in em or key not in am:differences.append({'relation':rel,'key':key,'kind':'extra' if key not in em else 'missing'});continue
   for col,v in em[key].items():
    if scope and scope[rel] and col not in scope[rel]:continue
    a=am[key][col]
    if isinstance(v,Decimal):same=a is not None and abs(D(a)-v)<D('.000001')
    else:same=a==v
    if not same:differences.append({'relation':rel,'key':key,'column':col,'expected':v,'actual':a})
 return differences
