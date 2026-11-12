"""Independent flat-source Python derivation for the declared harbor-affinity pipeline."""
import collections,hashlib,itertools,json,math

def derive_harbor(con,thresholds=None):
    thresholds=thresholds or {'support':0.0001,'confidence':0.01,'lift':1.2,'cooccurrence':1}
    queries={'lines':'SELECT ORDER_LINE_ID,ORDER_ID,PRODUCT_ID,PRODUCT_NAME FROM ORDERS.ORDER_LINES ORDER BY ORDER_LINE_ID','products':'SELECT PRODUCT_ID,PRODUCT_NAME,PRIMARY_CATEGORY_ID FROM PRODUCT.PRODUCTS ORDER BY PRODUCT_ID','categories':'SELECT CATEGORY_ID,CATEGORY_NAME FROM PRODUCT.PRODUCT_CATEGORIES ORDER BY CATEGORY_ID','orders':'SELECT ORDER_ID,STATUS FROM ORDERS.ORDERS ORDER BY ORDER_ID'}
    raw={k:con.execute(q).fetchall() for k,q in queries.items()};products={p:(name,c) for p,name,c in raw['products']};categories=dict(raw['categories']);headers=dict(raw['orders']);assert len(products)==len(raw['products']) and len(categories)==len(raw['categories']) and len(headers)==len(raw['orders'])
    stage=[];excluded=[];orphans=[];line_names=collections.defaultdict(set)
    for line,oid,p,line_name in raw['lines']:
        line_names[(oid,p)].add(line_name)
        if p is None or p not in products or products[p][1] not in categories:
            excluded.append({'line':line,'order_id':oid,'product_id':p,'reason':'No catalog product/category available for the requested descriptive fields.'});continue
        name,cat=products[p];stage.append({'order_id':oid,'product_id':p,'product_name':name,'category_id':cat,'category_name':categories[cat]})
        if oid not in headers:orphans.append({'line':line,'order_id':oid,'product_id':p})
    groups=collections.defaultdict(set);po=collections.defaultdict(set)
    for r in stage:groups[r['order_id']].add(r['product_id']);po[r['product_id']].add(r['order_id'])
    pairs=[];pair_orders=collections.defaultdict(set)
    for oid,ps in sorted(groups.items()):
        for a,b in itertools.combinations(sorted(ps),2):
            pair_orders[a,b].add(oid);pairs.append({'order_id':oid,'product_a_id':a,'product_a_name':products[a][0],'product_b_id':b,'product_b_name':products[b][0],'category_a_id':products[a][1],'category_b_id':products[b][1]})
    n=len(groups);rules=[]
    for(a,b),oids in sorted(pair_orders.items()):
        for a,b in [(a,b),(b,a)]:
            both=len(oids);na=len(po[a]);nb=len(po[b]);support=float(both)/n;confidence=float(both)/na;lift=confidence/(float(nb)/n);conviction=(1.-float(nb)/n)/(1.-confidence) if confidence!=1 else None
            rules.append({'product_a_id':a,'product_a_name':products[a][0],'product_b_id':b,'product_b_name':products[b][0],'support':support,'confidence':confidence,'lift':lift,'conviction':conviction,'orders_with_both':both,'orders_with_a':na,'orders_with_b':nb,'total_orders':n})
    facts=[]
    for r in rules:
        a,b=r['product_a_id'],r['product_b_id'];ca,cb=products[a][1],products[b][1]
        if ca!=cb and r['support']>=thresholds['support'] and r['confidence']>=thresholds['confidence'] and r['lift']>=thresholds['lift'] and r['orders_with_both']>=thresholds['cooccurrence']:
            facts.append({k:r[k] for k in ['product_a_id','product_a_name','product_b_id','product_b_name','support','confidence','lift','conviction','orders_with_both']}|{'category_a_id':ca,'category_b_id':cb})
    ranking=sorted(facts,key=lambda r:(-r['lift'],-r['confidence'],-r['orders_with_both'],r['product_a_id'],r['product_b_id']));bundles=[{'bundle_rank':i+1,**{k:r[k] for k in ['product_a_id','product_a_name','product_b_id','product_b_name','lift','confidence','orders_with_both']}} for i,r in enumerate(ranking[:50])]
    facts_raw={'sources':{k:{'sql':queries[k],'rows':len(v),'sha256':hashlib.sha256(json.dumps(v,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()} for k,v in raw.items()},'catalog_product_keys_unique':True,'catalog_category_keys_unique':True,'order_header_keys_unique':True,'excluded_unmapped_product_lines':excluded,'retained_order_header_orphans':orphans,'duplicate_line_identity_names':[{'order_id':o,'product_id':p,'line_names':sorted(ns)} for(o,p),ns in line_names.items() if len(ns)>1],'staging_rows':len(stage),'distinct_orders':n,'identity_pairs':len(pairs),'directional_rules':len(rules),'fact_rows':len(facts),'high_lift_pairs':sum(r['lift']>1.5 for r in facts),'chosen_thresholds':thresholds,'quality_policy':'Catalog-backed products/categories are retained; duplicate line occurrences remain in staging but are reduced by product identity for pairs and metrics. All source line order IDs are retained, including header orphans. The instruction imposes no explicit status/date filter and permits data-quality handling choices; no invented filter is imposed.','scope':'Independent flat-source Python joins, per-order sets, distinct-order intersections, all directional metric arithmetic and deterministic top50. No checker, expected output, native test or gold source is read.'}
    return {'main_staging.stg_basket__order_products':stage,'main_intermediate.int_affinity__product_pairs':pairs,'main_intermediate.int_affinity__association_rules':rules,'main_marts.fct_product_affinity_matrix':facts,'main_marts.rpt_recommended_bundles':bundles},facts_raw

def compare_harbor(expected,actual,only=None):
    differences=[]
    for relation,wanted in expected.items():
        if only and relation!=only['relation']:continue
        rows=actual[relation]['rows'] if isinstance(actual[relation],dict) else actual[relation]
        if relation.endswith('stg_basket__order_products'):
            w=collections.Counter(json.dumps(x,sort_keys=True,default=str) for x in wanted);a=collections.Counter(json.dumps(x,sort_keys=True,default=str) for x in rows)
            for text,count in (w-a).items():differences.append({'relation':relation,'kind':'missing_row','row':json.loads(text),'multiplicity':count})
            for text,count in (a-w).items():differences.append({'relation':relation,'kind':'extra_row','row':json.loads(text),'multiplicity':count})
            continue
        keys=['order_id','product_a_id','product_b_id'] if relation.endswith('product_pairs') else ['bundle_rank'] if relation.endswith('recommended_bundles') else ['product_a_id','product_b_id'];key=lambda r:tuple(r[k] for k in keys);w={key(r):r for r in wanted};a={key(r):r for r in rows}
        if len(a)!=len(rows):differences.append({'relation':relation,'kind':'duplicate_key'})
        for k in w.keys()-a.keys():differences.append({'relation':relation,'kind':'missing_key','key':k})
        for k in a.keys()-w.keys():differences.append({'relation':relation,'kind':'extra_key','key':k})
        for k in w.keys()&a.keys():
            for col,value in w[k].items():
                if only and col not in only.get('columns',w[k]):continue
                got=a[k][col];equal=math.isclose(float(got),value,rel_tol=1e-12,abs_tol=1e-10) if isinstance(value,float) and got is not None else got==value
                if not equal:differences.append({'relation':relation,'kind':'field','key':k,'column':col,'expected':value,'actual':got})
    return differences
