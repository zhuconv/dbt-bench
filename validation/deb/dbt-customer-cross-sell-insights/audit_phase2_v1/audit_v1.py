"""All55 declared cross-sell fields; original and explicit fixture scopes stay separate."""
from decimal import Decimal as D
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import derive
COLS={'rpt_cross_sell_insights':'sku_a sku_b orders_with_a orders_with_b orders_with_both support confidence_a_to_b confidence_b_to_a lift_a_to_b lift_b_to_a conviction_a_to_b leverage_a_to_b kulczynski_measure jaccard_coefficient cosine_similarity avg_revenue_per_order_with_both total_revenue_with_both revenue_weighted_support avg_quantity_per_order_with_both max_orders_in_single_day'.split(),'rpt_cross_sell_trends':'sku_a sku_b year quarter month orders_with_both support confidence_a_to_b lift_a_to_b quarter_over_quarter_change month_over_month_change exponential_decay_weight decay_weighted_support'.split(),'rpt_cross_sell_by_segment':'customer_segment sku_a sku_b orders_with_both support confidence_a_to_b lift_a_to_b avg_order_value_with_both category_a category_b same_category_flag cross_category_lift'.split(),'rpt_cross_sell_category_hierarchy':'category_a category_b orders_with_category_a orders_with_category_b orders_with_both_categories category_support category_confidence_a_to_b category_lift_a_to_b avg_skus_per_order_with_both total_revenue_with_both_categories'.split()}
KEYS={'rpt_cross_sell_insights':['sku_a','sku_b'],'rpt_cross_sell_trends':['sku_a','sku_b','year','quarter','month'],'rpt_cross_sell_by_segment':['customer_segment','sku_a','sku_b'],'rpt_cross_sell_category_hierarchy':['category_a','category_b']};TEXT={'sku_a','sku_b','customer_segment','category_a','category_b'};COUNTS=set('orders_with_a orders_with_b orders_with_both max_orders_in_single_day year quarter month orders_with_category_a orders_with_category_b orders_with_both_categories'.split());MONEY=set('avg_revenue_per_order_with_both total_revenue_with_both avg_quantity_per_order_with_both avg_order_value_with_both avg_skus_per_order_with_both total_revenue_with_both_categories'.split());INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def equal(a,b):
 if isinstance(b,D):return type(a)in[D,int,float]and D(str(a)).is_finite()and D(str(a))==b
 return type(a)is type(b)and a==b

def evaluate(outputs,expected,workflow=None):
 checks={}
 for n,cols in COLS.items():
  o=outputs.get(n,{});rows=o.get('rows');t=o.get('table');want={tuple(r[k]for k in KEYS[n]):r for r in expected['outputs']['analytics.'+n]}
  if o.get('exists')is False:checks[n+'.presence']=ck('fail','Declared output table absent; empty valid rowset still requires real typed table')
  if o.get('missing_required_columns'):checks[n+'.columns']=ck('fail','Explicit output columns absent',columns=o['missing_required_columns'])
  if rows is None:checks[n+'.values']=ck('pending','Actual output rows unavailable');continue
  if t is None:checks[n+'.interface']=ck('pending','Actual SQL schema unavailable')
  else:
   types={};cs=[]
   for col in cols:
    kind='text'if col in TEXT else'integer'if col in COUNTS else'boolean'if col=='same_category_flag'else'numeric';cs.append(Column(col,kind));types[col]=('VARCHAR',)if kind=='text'else INTS if kind=='integer'else('BOOLEAN',)if kind=='boolean'else('DECIMAL(18,2)',)if col in MONEY else('DECIMAL(18,6)',)
   a=audit_table(t,TablePolicy(TableContract(tuple(cs),keys=tuple(KEYS[n]),allow_extra_columns=True),types,relation_types=('TABLE','BASE TABLE')));checks[n+'.interface']=ck('pass'if a['reward']==1 else'pending'if a['status']=='unknown'else'fail','All declared SQL types including exact DECIMAL18,6/18,2; integer and BOOLEAN never coerced to floating identifiers',details=a)
  got={tuple(r.get(k)for k in KEYS[n]):r for r in rows};checks[n+'.population']=ck('pass'if set(got)==set(want)and len(got)==len(rows)else'fail','Exact canonical pair population/grain from raw sets; duplicates preserved',missing=[str(k)for k in want.keys()-got.keys()][:8],extra=[str(k)for k in got.keys()-want.keys()][:8],duplicates=len(rows)-len(got))
  for col in cols:
   bad=[{'key':str(k),'actual':str(r.get(col)),'independent':str(want[k][col])}for k,r in got.items()if k in want and not equal(r.get(col),want[k][col])];checks[n+'.'+col]=ck('fail'if bad else'pass','Independent eligible order/pair sets, exact group arithmetic and explicitly normalized temporal/category operations',mismatch_count=len(bad),mismatches=bad[:8])
  wrong=[]
  for r in rows:
   a,b=('category_a','category_b')if n=='rpt_cross_sell_category_hierarchy'else('sku_a','sku_b')
   if not isinstance(r.get(a),str)or not isinstance(r.get(b),str)or r[a]>=r[b]:wrong.append(str((r.get(a),r.get(b))))
  checks[n+'.canonical']=ck('fail'if wrong else'pass','Explicit lexicographic canonical pair orientation',errors=wrong[:8])
  bad=[]
  for r in rows:
   for col in cols:
    v=r.get(col)
    if col in TEXT|COUNTS or col=='same_category_flag'or v is None:continue
    places=2 if col in MONEY else 6
    if type(v)in[D,int,float]and D(str(v))!=D(str(v)).quantize(D(1).scaleb(-places)):bad.append({'column':col,'actual':str(v),'places':places})
  checks[n+'.precision']=ck('fail'if bad else'pass','Explicit monetary/quantity2 and rate6 rounding',errors=bad[:8],error_count=len(bad))
 checks['workflow']=workflow or ck('pending','Actual four-model build/source evidence unavailable');ss=[x['status']for x in checks.values()];reward=0 if'fail'in ss else None if'pending'in ss or'gap'in ss else 1
 return {'status':'unknown'if reward is None else'scored','reward':reward,'checks':checks,'model_requests':0,'native_rewards_modified':False,'input_scope':expected.get('scope','explicit original or separately declared extra fixture'),'profile':expected['profile']}
