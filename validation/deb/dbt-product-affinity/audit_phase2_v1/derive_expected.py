"""Independent selected-field reconstruction from instruction-declared public inputs.
No submitted models, reference SQL or verifier imported. Ambiguous fields are explicit.
"""
from collections import defaultdict,Counter
from decimal import Decimal,ROUND_HALF_UP
from itertools import combinations
import hashlib,json,datetime
D=lambda x:Decimal(str(x))
def rnd(x,n=2):return D(x).quantize(D(1).scaleb(-n),rounding=ROUND_HALF_UP)
def digest(v):return hashlib.sha256(json.dumps(v,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def derive_product(con):
    queries={'orders':'SELECT order_id,customer_id,ordered_at,grand_total,status,test_order_flag,is_first_order FROM main.stg_orders__orders ORDER BY order_id','lines':'SELECT order_line_id,order_id,product_id,product_name,quantity_ordered,line_total,status FROM main.stg_orders__order_lines ORDER BY order_line_id','products':'SELECT product_id,product_name,primary_category_id FROM main.stg_product__products ORDER BY product_id'}
    src={k:con.execute(q).fetchall() for k,q in queries.items()}
    assert len({r[0] for r in src['orders']})==len(src['orders']);assert len({r[0] for r in src['products']})==len(src['products'])
    orders={r[0]:r for r in src['orders'] if r[4] not in ['CANCELLED','RETURNED','FAILED'] and str(r[5]).upper() not in ['1','TRUE','T','Y','YES'] and '2023-01-01'<=str(r[2])<'2024-12-01'}
    lines=defaultdict(list)
    for r in src['lines']:
        if r[1] in orders and r[6] not in ['CANCELLED','RETURNED'] and r[4]>0:lines[(r[1],r[2])].append(r)
    baskets=defaultdict(dict)
    for (o,p),rs in lines.items():baskets[o][p]={'name':max(r[3] for r in rs),'rev':sum((D(r[5]) for r in rs),D(0))}
    baskets={o:ps for o,ps in baskets.items() if len(ps)>=2};N=len(baskets)
    byprod=defaultdict(set);bypair=defaultdict(set);customer_products=defaultdict(lambda:defaultdict(dict));stage=[]
    for o,ps in baskets.items():
        h=orders[o]
        for p,v in ps.items():
            byprod[p].add(o);customer_products[h[1]][p][o]=h[2]
            stage.append(dict(order_id=o,customer_id=h[1],product_id=p,product_name=v['name'],product_order_revenue=v['rev'],ordered_at=h[2],is_first_order=h[6],grand_total=h[3]))
        for pair in combinations(sorted(ps),2):bypair[pair].add(o)
    stats={p:dict(product_id=p,product_name=max(baskets[o][p]['name'] for o in os),product_order_count=len(os),product_total_revenue=rnd(sum(baskets[o][p]['rev'] for o in os)),total_orders=N) for p,os in byprod.items()}
    kept={pair:os for pair,os in bypair.items() if len(os)>=3 and D(len(os))/N>=D('.0001')}
    ranks={n:i+1 for i,n in enumerate(sorted({len(os) for os in kept.values()},reverse=True))}
    mart=[];pairs=[];trends=[];seq_proof=[];classification_overlap=[]
    early={o for o in baskets if str(orders[o][2])<'2024-01-01'};recent=set(baskets)-early
    for (a,b),os in sorted(kept.items()):
        n=len(os);na=len(byprod[a]);nb=len(byprod[b]);ca=D(n)/na;cb=D(n)/nb;lift=D(n)*N/(na*nb);ec=len(os&early);rc=len(os&recent)
        ft=sum(orders[o][6] is True or orders[o][6]==1 for o in os)
        revenue=sum((baskets[o][a]['rev']+baskets[o][b]['rev'] for o in os),D(0));basket=sum((D(orders[o][3]) for o in os),D(0))/n
        def cls(l):return 'Strong Positive' if l>=3 else 'Moderate Positive' if l>=D('1.5') else 'Weak Positive' if l>1 else 'Independent' if abs(l-1)<D('.01') else 'Negative'
        if 0<abs(lift-1)<D('.01'):classification_overlap.append({'pair':[a,b],'lift':str(lift)})
        el=D(ec)*len(early)/(len(byprod[a]&early)*len(byprod[b]&early)) if ec else D(0)
        rl=D(rc)*len(recent)/(len(byprod[a]&recent)*len(byprod[b]&recent)) if rc else D(0)
        trend='New' if ec==0 else 'Discontinued' if rc==0 else 'Strengthening' if rl>el*D('1.2') else 'Weakening' if rl<el*D('.8') else 'Stable'
        pct=rnd(D(ft)*100/n,1);segment='First-Time Favorite' if pct>=60 else 'Repeat Favorite' if pct<=40 else 'Universal'
        validcust=[];excluded=[];overlap=[];same_day_diff_time=[]
        for c,ps in customer_products.items():
            if a not in ps or b not in ps:continue
            distinct=[(oa,ob,ta,tb) for oa,ta in ps[a].items() for ob,tb in ps[b].items() if oa!=ob]
            if not distinct:excluded.append(c);continue
            validcust.append(c);fa=min(ps[a].values());fb=min(ps[b].values())
            if set(ps[a])&set(ps[b]):overlap.append(c)
            if fa!=fb and fa.date()==fb.date():same_day_diff_time.append(c)
        seq_proof.append({'pair':[a,b],'expected_customers':validcust,'excluded_coorder_only_customers':excluded,'separate_orders_but_also_cobasket':overlap,'same_day_different_timestamp':same_day_diff_time})
        base=dict(product_a_id=a,product_b_id=b,co_purchase_count=n,product_a_orders=na,product_b_orders=nb,total_orders=N,support=rnd(D(n)/N,6),confidence_a_to_b=rnd(ca,4),confidence_b_to_a=rnd(cb,4),lift=rnd(lift,4),jaccard=rnd(D(n)/(na+nb-n),4),affinity_class=cls(lift),stronger_direction='A_to_B' if ca>cb else 'B_to_A' if cb>ca else 'Equal',pair_revenue=rnd(revenue),avg_pair_basket_value=rnd(basket),pair_rank=ranks[n],early_co_purchase_count=ec,recent_co_purchase_count=rc,trend_classification=trend,first_time_co_purchases=ft,repeat_co_purchases=n-ft,first_time_pct=pct,customer_affinity_segment=segment,sequential_customers=len(validcust))
        mart.append(base);pairs.append({k:base[k] for k in ['product_a_id','product_b_id','co_purchase_count','pair_revenue','avg_pair_basket_value','early_co_purchase_count','recent_co_purchase_count','first_time_co_purchases','repeat_co_purchases']})
        months=defaultdict(set)
        for o in os:months[str(orders[o][2])[:7]].add(o)
        cumulative=0;cumrev=D(0)
        for i,(month,mos) in enumerate(sorted(months.items()),1):
            mr=rnd(sum((baskets[o][a]['rev']+baskets[o][b]['rev'] for o in mos),D(0)));cumulative+=len(mos);cumrev+=mr
            trends.append(dict(product_a_id=a,product_b_id=b,order_month=month,monthly_co_purchases=len(mos),monthly_pair_revenue=mr,cumulative_co_purchases=cumulative,cumulative_pair_revenue=cumrev,month_rank=i,pct_of_total_co_purchases=rnd(D(len(mos))*100/n,1),monthly_first_time_pct=rnd(D(sum(orders[o][6] is True or orders[o][6]==1 for o in mos))*100/len(mos),1)))
    summary=[]
    for p,os in sorted(byprod.items()):
        if len(os)<10:continue
        assoc=sorted((r for r in mart if p in [r['product_a_id'],r['product_b_id']]),key=lambda r:(-r['co_purchase_count'],r['product_b_id'] if p==r['product_a_id'] else r['product_a_id']))[:5]
        for i,r in enumerate(assoc,1):
            other=r['product_b_id'] if p==r['product_a_id'] else r['product_a_id'];summary.append(dict(product_id=p,product_total_orders=len(os),product_total_revenue=stats[p]['product_total_revenue'],rank=i,associated_product_id=other,co_purchase_count=r['co_purchase_count'],confidence=r['confidence_a_to_b'] if p==r['product_a_id'] else r['confidence_b_to_a'],lift=r['lift'],trend_classification=r['trend_classification'],customer_affinity_segment=r['customer_affinity_segment']))
    cats={r[0]:r[2] for r in src['products']};catorders=defaultdict(set);catpairs=defaultdict(set);cat_prod_pairs=defaultdict(set)
    for o,ps in baskets.items():
        cs={cats[p] for p in ps if p in cats}
        for c in cs:catorders[c].add(o)
        for pair in combinations(sorted(cs),2):catpairs[pair].add(o)
    for a,b in kept:
        if a in cats and b in cats and cats[a]!=cats[b]:cat_prod_pairs[tuple(sorted([cats[a],cats[b]]))].add((a,b))
    category=[]
    for (a,b),os in sorted(catpairs.items()):
        category.append(dict(category_a_id=a,category_b_id=b,co_purchase_count=len(os),category_a_orders=len(catorders[a]),category_b_orders=len(catorders[b]),avg_products_per_pair_order=rnd(D(sum(len(baskets[o]) for o in os))/len(os)),unique_product_pairs_lower_bound=len(cat_prod_pairs[(a,b)])))
    results={'main.int_affinity_order_products':stage,'main.int_affinity_product_stats':list(stats.values()),'main.int_affinity_product_pairs':pairs,'main.product_affinity':mart,'main.product_affinity_summary':summary,'main.category_affinity':category,'main.affinity_trends':trends}
    metadata={'sources':{k:{'query':queries[k],'count':len(v),'sha256':digest(v)} for k,v in src.items()},'qualifying_orders':N,'qualifying_order_products':len(stage),'source_null_counts':{k:[sum(r[i] is None for r in v) for i in range(len(v[0]))] for k,v in src.items()},'sequence_membership_proof':seq_proof,'executed_ambiguity_investigations':{'classification_overlap_actual_pairs':classification_overlap,'sequence_also_coorder_customer_pair_count':sum(len(x['separate_orders_but_also_cobasket']) for x in seq_proof),'same_day_timestamp_direction_customer_pair_count':sum(len(x['same_day_different_timestamp']) for x in seq_proof),'grand_total_qualifying_orders':str(sum((D(orders[o][3]) for o in baskets),D(0))),'line_total_qualifying_orders':str(sum((v['rev'] for ps in baskets.values() for v in ps.values()),D(0))),'scope':'Revenue contribution denominator header total versus eligible product revenue differs; instruction says total revenue of qualifying orders without resolving those source notions. Sequence membership is certain, direction for customers with co-order plus separate-order histories remains unstated. Advanced metrics rounding from exact or already-rounded confidence not resolved.'},'expected_scope':'Selected independently decidable fields only; no full lawful baseline claimed; category unique pairs is conservative threshold-qualified lower bound, not a fabricated exact answer.'}
    return results,metadata

def compare_product(expected,actual,scope=None):
    keys={'main.int_affinity_order_products':['order_id','product_id'],'main.int_affinity_product_stats':['product_id'],'main.int_affinity_product_pairs':['product_a_id','product_b_id'],'main.product_affinity':['product_a_id','product_b_id'],'main.product_affinity_summary':['product_id','rank'],'main.category_affinity':['category_a_id','category_b_id'],'main.affinity_trends':['product_a_id','product_b_id','order_month']};diff=[]
    for rel,rows in expected.items():
        if scope and rel not in scope:continue
        kk=keys[rel];obs=actual[rel]['rows'];em={tuple(r[k] for k in kk):r for r in rows};am={tuple(r[k] for k in kk):r for r in obs}
        if len(am)!=len(obs):diff.append({'relation':rel,'kind':'duplicate_keys'})
        for k in sorted(set(em)|set(am)):
            if k not in em or k not in am:diff.append({'relation':rel,'key':k,'kind':'extra' if k not in em else 'missing'});continue
            for col,v in em[k].items():
                if scope and scope[rel] and col not in scope[rel]:continue
                if col=='unique_product_pairs_lower_bound':
                    if am[k]['unique_product_pairs']<v:diff.append({'relation':rel,'key':k,'column':'unique_product_pairs','expected_at_least':v,'actual':am[k]['unique_product_pairs']})
                    continue
                a=am[k][col]
                if isinstance(v,Decimal):same=a is not None and abs(D(a)-v)<D('.000001')
                elif isinstance(v,datetime.datetime):same=str(a)==str(v)
                else:same=a==v
                if not same:diff.append({'relation':rel,'key':k,'column':col,'expected':v,'actual':a})
    return diff
