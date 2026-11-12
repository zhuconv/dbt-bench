"""Independent keyed field audit. Policy alternatives remain visible and unscored."""
from decimal import Decimal as D
from collections import Counter
import re
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v2 import COLS,KEYS
KIND='lifo'
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
INTEGER=set('quantity quantity_sold batches_consumed inventory_shortfall total_purchased_qty total_sold_qty remaining_inventory_qty total_quantity_sold order_count months_active revenue_rank quantity_rank product_count total_quantity high_velocity_count medium_velocity_count low_velocity_count total_products a_class_count b_class_count c_class_count'.split())
TEXT=set('purchase_id po_id sale_id order_id sku original_currency currency costing_method from_currency to_currency product_id product_name category_id abc_class first_sale_month last_sale_month revenue_velocity category_concentration'.split())
DATES={'purchase_date','sale_date','rate_date'}
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def eq(a,b):
 if a is None or b is None:return a is b
 if isinstance(b,bool):return type(a)is bool and a==b
 if isinstance(b,(D,int))and type(b)is not bool:
  if type(a)not in[int,float,D]:return False
  return D(str(a)).is_finite()and D(str(a))==D(b)
 return type(a)is type(b)and a==b

def evaluate(outputs,expected,workflow=None):
 checks={};rows={};policies=expected['policies']if KIND=='abc'else{'zero_empty_pool':expected['rows'],'null_empty_pool':expected['alternate_undefined_pool_null']};first=next(iter(policies.values()))
 for name,cols in COLS.items():
  o=outputs.get(name,{});t=o.get('table');rs=o.get('rows')
  if o.get('exists')is False:checks[name+'.presence']=ck('fail','Declared output relation absent')
  if o.get('missing_required_columns'):checks[name+'.columns']=ck('fail','Declared output column absent',columns=o['missing_required_columns'])
  if rs is None:checks[name+'.values']=ck('pending','Actual output rows not captured');continue
  rows[name]=rs
  if t is None:checks[name+'.interface']=ck('pending','Physical SQL schema unavailable')
  else:
   types={};cs=[]
   for c in cols:
    typ=t.physical_sql_types.get(c,'');kind='text'if c in TEXT else'date'if c in DATES else'boolean'if c=='is_pareto_product'else'integer'if c in INTEGER else'numeric';cs.append(Column(c,kind))
    types[c]=('VARCHAR',)if c in TEXT else('DATE',)if c in DATES else('BOOLEAN',)if c=='is_pareto_product'else INTS if c in INTEGER else(typ,)if re.fullmatch(r'DECIMAL\(\d+,\d+\)',typ)else('DECIMAL',)
    if name.startswith('int_')and KIND=='lifo'and c in ['unit_cost','rate_to_usd']:types[c]=(typ,)if re.fullmatch(r'DECIMAL\(\d+,\d+\)',typ)else('FLOAT','DOUBLE')
   typereq=('TABLE','BASE TABLE')if KIND=='abc'and not name.startswith('int_')else('VIEW',)if KIND=='abc'else('TABLE','BASE TABLE','VIEW')
   a=audit_table(t,TablePolicy(TableContract(tuple(cs),keys=tuple(KEYS[name]),allow_extra_columns=True),types,not_null=tuple(cols)if KIND=='abc'and name=='abc_classification'else(),relation_types=typereq));checks[name+'.interface']=ck('pending'if a['status']=='unknown'else'pass'if a['reward']==1 else'fail','Exact identifiers/types and keyed multiplicity; declared decimal measures required, no global float tolerance',details=a)
  keys=KEYS[name];wanted={tuple(r[k]for k in keys):r for r in first[name]};got={tuple(r.get(k)for k in keys):r for r in rs};counts=Counter(tuple(r.get(k)for k in keys)for r in rs);population_ok=set(got)==set(wanted)and len(got)==len(rs);checks[name+'.population']=ck('pass'if population_ok else'fail','Independent original-input membership and duplicate keys',missing=[str(k)for k in wanted.keys()-got.keys()][:8],extra=[str(k)for k in got.keys()-wanted.keys()][:8],duplicates=[str(k)for k,v in counts.items()if v>1][:8]);maps=[{tuple(r[k]for k in keys):r for r in ps[name]}for ps in policies.values()]
  for col in cols:
   errors=[];ambiguity=[]
   for k,r in got.items():
    if k not in wanted:continue
    options=[m[k][col]for m in maps if k in m];value=r.get(col)
    if KIND=='abc'and col in ['product_name','category_id']:
     pid=r.get('product_id');amb=col=='product_name'and pid in expected['profile']['ambiguous_line_names']or col=='category_id'and pid in expected['profile']['missing_product_metadata']
     if amb:ambiguity.append(str(k));continue
    if KIND=='lifo'and any(x is None for x in options)and any(x is not None for x in options):ambiguity.append(str(k));continue
    if any(not eq(x,options[0])for x in options):
     if any(eq(value,x)for x in options):ambiguity.append(str(k));continue
    if not any(eq(value,x)for x in options):errors.append({'key':str(k),'actual':str(value),'independent_options':list(dict.fromkeys(map(str,options)))})
   checks[name+'.'+col]=ck('fail'if errors else'gap'if ambiguity else'pass','Source-derived keyed field comparison across disclosed policies; no checker output defines validity',mismatches=errors[:8],mismatch_count=len(errors),ambiguous_keys=ambiguity[:8],ambiguous_count=len(ambiguity))
 if KIND=='lifo':
  for r in rows.get('sale_cogs',[]):
   values=[r.get(c)for c in ['cogs_usd','fallback_cost_usd','total_estimated_cogs']]
   if all(type(v)in[int,float,D]for v in values)and D(str(values[0]))+D(str(values[1]))!=D(str(values[2])):checks['conditional_total_estimated_cogs']=ck('fail','Actual total must equal actual COGS plus actual fallback, independent of empty-pool policy');break
  else:checks['conditional_total_estimated_cogs']=ck('pass','No observed contradiction of declared additive total; undefined NULL arithmetic remains policy gap')
  checks['undefined_pool_policy']=ck('gap','Actual zero and NULL alternatives differ: no purchase cost pool for sales, no sales for purchase-only SKUs; contract does not specify empty-denominator defaults',profile=expected['profile'])
  checks['unexercised_allocation_fx']=ck('gap','Actual original inputs have only USD eligible purchases and zero purchase/sale SKU overlap; existing six changed builds are equal-on-data. These branches are not certified.')
 else:
  checks['metadata_policy']=ck('gap','Actual source conflicts and missing metadata prevent unique whole-task outputs; names/categories are not inferred from reference',missing_products=expected['profile']['missing_product_metadata'],conflicting_names=len(expected['profile']['ambiguous_line_names']))
  checks['rounding_policy']=ck('gap'if expected['profile']['unit_half_cent_cases']else'pass','Executed half-up versus half-even and raw versus rounded boundary arithmetic',half_cent_cases=expected['profile']['unit_half_cent_cases'])
  checks['concentration_placement']=ck('gap','Independent positions and threshold counts computed, but contract does not place three requested metrics in its declared columns',expected=expected['policy_details'])
  c=rows.get('abc_classification');summary=rows.get('abc_summary');cats=rows.get('abc_category_breakdown')
  # Broad row errors remain source-derived above; category fixed UNKNOWN policy is explicitly conditional.
  if expected['profile']['missing_product_metadata']and cats is not None:
   for col in COLS['abc_category_breakdown']:
    k='abc_category_breakdown.'+col
    if k in checks:checks[k]['scope']='Conditional UNKNOWN missing-category policy; cannot count as target error without independent mapping proof.'
 checks['workflow']=workflow or ck('pending','Trusted execution provenance absent');states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if'gap'in states or'pending'in states else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
