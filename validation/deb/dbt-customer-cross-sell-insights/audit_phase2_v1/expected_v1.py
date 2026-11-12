# Independent set arithmetic adapted from preserved source; direct category hierarchy reads actual variant.category separately from segment category mapping.
# Original engine SHA256: 81619fd2189d63eacae7161e598e667e5a24aa7272cbcdafab7d775346572103
"""Independent public-input derivation. Empty original universe handled without inventing data."""
from collections import defaultdict,Counter
from itertools import combinations
from decimal import Decimal,ROUND_HALF_UP
import hashlib,json,math
D=lambda x:Decimal(str(x))
def rnd(x,n=6):return None if x is None else D(x).quantize(D(1).scaleb(-n),rounding=ROUND_HALF_UP)
def digest(v):return hashlib.sha256(json.dumps(v,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
REL=['analytics.rpt_cross_sell_insights','analytics.rpt_cross_sell_trends','analytics.rpt_cross_sell_by_segment','analytics.rpt_cross_sell_category_hierarchy']
def derive(data):
 queries={'lines':'SELECT order_id,sku,quantity_ordered FROM main.int_sales__order_lines ORDER BY ALL','orders':'SELECT order_id,customer_id,grand_total,ordered_at,is_cancelled FROM main.int_sales__orders_enriched ORDER BY ALL','customers':'SELECT customer_id FROM main.int_customers__unified ORDER BY ALL','variants':'SELECT sku,product_id FROM main.dim_product_variants ORDER BY ALL','mapping':'SELECT product_id,category_id,is_primary,sort_order FROM main.stg_product__product_category_mapping ORDER BY ALL','categories':'SELECT category_id,category_name FROM main.stg_product__product_categories ORDER BY ALL'}
 fields={'lines':['order_id','sku','quantity_ordered'],'orders':['order_id','customer_id','grand_total','ordered_at','is_cancelled'],'customers':['customer_id'],'variants':['sku','product_id'],'mapping':['product_id','category_id','is_primary','sort_order'],'categories':['category_id','category_name']}
 raw={k:[tuple(r[c]for c in fields[k])for r in rows]for k,rows in data.items()if k in fields};headers=defaultdict(list);skus=defaultdict(set);qty=defaultdict(lambda:D(0))
 for r in raw['orders']:
  if r[0] is not None and r[4] in [False,None,0]:headers[r[0]].append(r)
 for o,s,q in raw['lines']:
  if q is not None:qty[o]+=D(q)
  if o in headers and s is not None:skus[o].add(s)
 eligible={o:ss for o,ss in skus.items() if len(ss)>=2};N=len(eligible);marg=defaultdict(set);pairs=defaultdict(set)
 for o,ss in eligible.items():
  for s in ss:marg[s].add(o)
  for pair in combinations(sorted(ss),2):pairs[pair].add(o)
 kept={p:os for p,os in pairs.items() if len(os)>=3 and D(len(os))/N>=D('.005') and D(len(os))/(len(marg[p[0]])+len(marg[p[1]])-len(os))>=D('.001')}
 sku_prod={r[0]:r[1] for r in raw['variants']};names={r[0]:r[1] for r in raw['categories']};mp=defaultdict(list)
 for p,c,primary,sort in raw['mapping']:mp[p].append((not primary,sort,c))
 sku_cat={s:names.get(sorted(mp[p])[0][2]) if mp[p] else None for s,p in sku_prod.items()};catorders=defaultdict(set);catpairs=defaultdict(set)
 hierarchy_categories={r['sku']:r.get('category')for r in data['variants']}
 for o,ss in eligible.items():
  cs={hierarchy_categories.get(s) for s in ss}-{None}
  for c in cs:catorders[c].add(o)
  for pair in combinations(sorted(cs),2):catpairs[pair].add(o)
 keptcats={p:os for p,os in catpairs.items() if len(os)>=5 and D(len(os))/N>=D('.01')}
 meta={'sources':{k:{'query':queries[k],'rows':len(v),'sha256':digest(v)} for k,v in raw.items()},'eligible_order_ids':N,'raw_pairs':len(pairs),'max_distinct_pair_orders':max(map(len,pairs.values()),default=0),'retained_pairs':len(kept),'retained_category_pairs':len(keptcats),'eligible_sku_variant_matches':len(set().union(*eligible.values())&set(sku_prod)) if eligible else 0,'duplicate_eligible_headers':sum(len(headers[o])>1 for o in eligible),'no_native_expected_constants_used':True,'hierarchy_category_column_present':any('category'in r for r in data['variants']),'hierarchy_uses_direct_variant_category':True}
 if not kept and not keptcats:return {'outputs':{r:[]for r in REL},'profile':meta}
 # Nonempty controlled fixture deliberately has unique, complete headers. No policy guesses for conflicting originals.
 assert all(len(headers[o])==1 for o in eligible);orders={o:rs[0] for o,rs in headers.items()};assert all(orders[o][3] is not None for o in eligible)
 totalrev=sum((D(orders[o][2]) for o in eligible),D(0));base=[]
 for (a,b),os in sorted(kept.items()):
  n=len(os);na=len(marg[a]);nb=len(marg[b]);ca=D(n)/na;cb=D(n)/nb;support=D(n)/N;rev=sum((D(orders[o][2]) for o in os),D(0));daily=Counter(orders[o][3].date() for o in os)
  base.append(dict(sku_a=a,sku_b=b,orders_with_a=na,orders_with_b=nb,orders_with_both=n,support=rnd(support),confidence_a_to_b=rnd(ca),confidence_b_to_a=rnd(cb),lift_a_to_b=rnd(D(n)*N/(na*nb)),lift_b_to_a=rnd(D(n)*N/(na*nb)),conviction_a_to_b=None if ca==1 else rnd((1-D(nb)/N)/(1-ca)),leverage_a_to_b=rnd(support-D(na)*nb/(N*N)),kulczynski_measure=rnd((ca+cb)/2),jaccard_coefficient=rnd(D(n)/(na+nb-n)),cosine_similarity=rnd(D(n)/(D(na)*nb).sqrt()),avg_revenue_per_order_with_both=rnd(rev/n,2),total_revenue_with_both=rnd(rev,2),revenue_weighted_support=rnd(rev/totalrev),avg_quantity_per_order_with_both=rnd(sum(qty[o] for o in os)/n,2),max_orders_in_single_day=max(daily.values())))
 period_orders=defaultdict(set);quarter_orders=defaultdict(set)
 for o in eligible:
  dt=orders[o][3];period_orders[(dt.year,dt.month)].add(o);quarter_orders[(dt.year,(dt.month-1)//3+1)].add(o)
 trends=[]
 for (a,b),os in sorted(kept.items()):
  periods={period:os&universe for period,universe in period_orders.items() if os&universe};latest=max(orders[o][3].date() for o in os)
  quarter_support={(y,q):D(len(os&universe))/len(universe) for (y,q),universe in quarter_orders.items() if os&universe}
  monthly_support={period:rnd(D(len(pos))/len(period_orders[period])) for period,pos in periods.items()}
  for (y,m),pos in sorted(periods.items()):
   q=(m-1)//3+1;universe=period_orders[(y,m)];n=len(pos);na=len(marg[a]&universe);nb=len(marg[b]&universe);support=monthly_support[(y,m)];prev=(y,m-1) if m>1 else (y-1,12);ps=monthly_support.get(prev);qs=quarter_support.get((y,q-1)) if q>1 else None;currentq=quarter_support[(y,q)];decay=D(str(math.exp(-.1*(latest-max(orders[o][3].date() for o in pos)).days)))
   trends.append(dict(sku_a=a,sku_b=b,year=y,quarter=q,month=m,orders_with_both=n,support=support,confidence_a_to_b=rnd(D(n)/na),lift_a_to_b=rnd(D(n)*len(universe)/(na*nb)),quarter_over_quarter_change=None if qs is None or qs==0 else rnd((currentq-qs)/qs),month_over_month_change=None if ps is None or ps==0 else rnd((support-ps)/ps),exponential_decay_weight=rnd(decay),decay_weighted_support=rnd(support*decay)))
 clv=defaultdict(lambda:D(0));known_customers={r[0] for r in raw['customers']}
 for r in raw['orders']:
  if r[4] in [False,None,0] and r[1] is not None:clv[r[1]]+=D(r[2])
 def segment(c):
  v=clv.get(c);return 'unknown' if v is None else 'high_value' if v>=10000 else 'medium_value' if v>=5000 else 'low_value' if v>=1000 else 'new'
 segorders=defaultdict(set)
 for o in eligible:assert orders[o][1] in known_customers;segorders[segment(orders[o][1])].add(o)
 segmented=[]
 for seg,universe in sorted(segorders.items()):
  for (a,b),allos in sorted(kept.items()):
   os=allos&universe
   if not os:continue
   n=len(os);na=len(marg[a]&universe);nb=len(marg[b]&universe);lift=rnd(D(n)*len(universe)/(na*nb));ac=sku_cat.get(a);bc=sku_cat.get(b);same=ac is not None and ac==bc
   segmented.append(dict(customer_segment=seg,sku_a=a,sku_b=b,orders_with_both=n,support=rnd(D(n)/len(universe)),confidence_a_to_b=rnd(D(n)/na),lift_a_to_b=lift,avg_order_value_with_both=rnd(sum(D(orders[o][2]) for o in os)/n,2),category_a=ac,category_b=bc,same_category_flag=same,cross_category_lift=None if same else lift))
 category=[]
 for (a,b),os in sorted(keptcats.items()):
  n=len(os);na=len(catorders[a]);nb=len(catorders[b]);category.append(dict(category_a=a,category_b=b,orders_with_category_a=na,orders_with_category_b=nb,orders_with_both_categories=n,category_support=rnd(D(n)/N),category_confidence_a_to_b=rnd(D(n)/na),category_lift_a_to_b=rnd(D(n)*N/(na*nb)),avg_skus_per_order_with_both=rnd(D(sum(len(eligible[o]) for o in os))/n,2),total_revenue_with_both_categories=rnd(sum(D(orders[o][2]) for o in os),2)))
 return {'outputs':dict(zip(REL,[base,trends,segmented,category])),'profile':meta}
