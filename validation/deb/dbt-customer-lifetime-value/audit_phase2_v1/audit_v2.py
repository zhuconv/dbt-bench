"""Declared CLV final tables and independent input replay; lifecycle holes remain explicit."""
from collections import Counter
from decimal import Decimal as D,ROUND_HALF_UP,ROUND_HALF_DOWN
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import derive
import re
COLS={'clv_segments':'customer_id customer_name first_order_date last_order_date customer_tenure_days days_since_last_order total_orders total_revenue avg_order_value avg_days_between_orders orders_per_year predicted_annual_revenue clv_3_year clv_3_year_npv clv_segment is_at_risk clv_percentile clv_quartile lifecycle_stage first_order_quarter value_trajectory velocity_trend churn_probability_score revenue_contribution_pct cumulative_revenue_pct revenue_rank engagement_score expected_next_order_date days_until_expected_order days_overdue'.split(),'clv_segment_summary':'clv_segment customer_count total_revenue avg_clv_3_year avg_clv_3_year_npv avg_orders_per_year at_risk_count at_risk_percentage churned_count avg_tenure_days avg_engagement_score avg_churn_probability accelerating_count decelerating_count pct_revenue_contribution'.split(),'clv_cohort_analysis':'cohort_quarter cohort_size total_cohort_revenue avg_clv_3_year avg_clv_3_year_npv avg_orders retention_rate at_risk_rate avg_engagement_score avg_churn_probability platinum_count gold_count silver_count bronze_count accelerating_pct decelerating_pct'.split(),'clv_monthly_trends':'customer_id order_month monthly_orders monthly_revenue cumulative_orders cumulative_revenue months_since_first_order avg_monthly_revenue is_active_month order_month_rank'.split()}
KEYS={'clv_segments':['customer_id'],'clv_segment_summary':['clv_segment'],'clv_cohort_analysis':['cohort_quarter'],'clv_monthly_trends':['customer_id','order_month']}
TEXT=set('customer_id customer_name clv_segment lifecycle_stage first_order_quarter value_trajectory velocity_trend cohort_quarter order_month'.split());COUNTS=set('customer_tenure_days days_since_last_order total_orders clv_quartile revenue_rank days_until_expected_order days_overdue customer_count at_risk_count churned_count accelerating_count decelerating_count cohort_size platinum_count gold_count silver_count bronze_count monthly_orders cumulative_orders months_since_first_order order_month_rank'.split());BOOLS={'is_at_risk','is_active_month'};TIMES={'first_order_date','last_order_date'};ONE=set('avg_days_between_orders clv_percentile churn_probability_score revenue_contribution_pct cumulative_revenue_pct engagement_score at_risk_percentage avg_tenure_days avg_engagement_score avg_churn_probability pct_revenue_contribution retention_rate at_risk_rate accelerating_pct decelerating_pct'.split());INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def policies(raw):return[derive(raw,r)for r in[ROUND_HALF_UP,ROUND_HALF_DOWN]]
def equal(a,b):
 if isinstance(b,D):return type(a)in[D,int,float]and D(str(a)).is_finite()and D(str(a))==b
 return type(a)is type(b)and a==b

def evaluate(outputs,expected,workflow=None):
 checks={};variants=expected['policies'];undefined={r['customer_id']for r in variants[0]['profile']['undefined_conditions']};basecustomers={r['customer_id']:r for r in variants[0]['outputs']['main.clv_segments']};unknownsegments={basecustomers[c]['clv_segment']for c in undefined};unknowncohorts={basecustomers[c]['first_order_quarter']for c in undefined}
 # Per-customer internally coherent halfway choices may differ across customers.
 # Aggregated means must use the resulting validated rounded customer amounts.
 source_maps=[{r['customer_id']:r for r in v['outputs']['main.clv_segments']}for v in variants];actual=outputs.get('clv_segments',{}).get('rows',[]);source_fields='total_revenue avg_order_value avg_days_between_orders orders_per_year predicted_annual_revenue clv_3_year clv_3_year_npv'.split();chosen={};unresolved=[]
 for row in actual:
  cid=row.get('customer_id');options=[m[cid]for m in source_maps if cid in m];matched=[r for r in options if all(equal(row.get(c),r[c])for c in source_fields)]
  if matched:chosen[cid]=matched[0]
  else:unresolved.append(cid)
 coherent=not unresolved and len(actual)==len(chosen)==len(source_maps[0])and set(chosen)==set(source_maps[0]);checks['rounding_coherence']=ck('pass'if coherent else'gap','Actual customer source-calculation vectors checked against raw-derived sequential nearest-rounding branches before using downstream aggregates; unknown mixed within-customer branches not forced incorrect',matched_customers=len(chosen),unresolved_customers=unresolved)
 aggregate_options={}
 if coherent:
  for n,keycol in [('clv_segment_summary','clv_segment'),('clv_cohort_analysis','first_order_quarter')]:
   for group in {r[keycol]for r in chosen.values()}:
    rs=[r for r in chosen.values()if r[keycol]==group]
    for target,source in [('avg_clv_3_year','clv_3_year'),('avg_clv_3_year_npv','clv_3_year_npv')]:
     value=sum(r[source]for r in rs)/len(rs);aggregate_options[(n,(group,),target)]=[value.quantize(D('.01'),rounding=r)for r in[ROUND_HALF_UP,ROUND_HALF_DOWN]]
 for n,cols in COLS.items():
  o=outputs.get(n,{});rows=o.get('rows');t=o.get('table');want=[{tuple(r[k]for k in KEYS[n]):r for r in v['outputs']['main.'+n]}for v in variants]
  if o.get('exists')is False:checks[n+'.presence']=ck('fail','Declared output absent')
  if o.get('missing_required_columns'):checks[n+'.columns']=ck('fail','Required explicit output column absent',columns=o['missing_required_columns'])
  if rows is None:checks[n+'.values']=ck('pending','Actual rows unavailable');continue
  if t is None:checks[n+'.interface']=ck('pending','Actual physical SQL schema unavailable')
  else:
   types={};cs=[]
   for col in cols:
    kind='text'if col in TEXT else'integer'if col in COUNTS else'boolean'if col in BOOLS else'datetime'if col in TIMES else'date'if col=='expected_next_order_date'else'numeric';cs.append(Column(col,kind));typ=t.physical_sql_types.get(col,'');types[col]=('VARCHAR',)if kind=='text'else INTS if kind=='integer'else('BOOLEAN',)if kind=='boolean'else('TIMESTAMP','TIMESTAMP WITH TIME ZONE')if kind=='datetime'else('DATE',)if kind=='date'else(typ,)if re.fullmatch(r'DECIMAL\(\d+,\d+\)',typ)else('DECIMAL',)
   a=audit_table(t,TablePolicy(TableContract(tuple(cs),keys=tuple(KEYS[n]),allow_extra_columns=True),types,not_null=tuple(cols)if n=='clv_segments'else(),relation_types=('TABLE','BASE TABLE')));checks[n+'.interface']=ck('pass'if a['reward']==1 else'pending'if a['status']=='unknown'else'fail','Explicit final table columns/types, nonnull CLV segment fields and keyed grain',details=a)
  got={tuple(r.get(k)for k in KEYS[n]):r for r in rows};checks[n+'.population']=ck('pass'if set(got)==set(want[0])and len(got)==len(rows)else'fail','Original qualifying minimum-two-order customer universe and per-table grain; exact multiplicity',missing=[str(x)for x in want[0].keys()-got.keys()][:8],extra=[str(x)for x in got.keys()-want[0].keys()][:8],duplicates=len(rows)-len(got))
  for col in cols:
   bad=[];variable=[];holes=[]
   for key,row in got.items():
    if key not in want[0]:continue
    if(n=='clv_segments'and col=='lifecycle_stage'and key[0]in undefined)or(n=='clv_segment_summary'and col=='churned_count'and key[0]in unknownsegments)or(n=='clv_cohort_analysis'and col=='retention_rate'and key[0]in unknowncohorts):holes.append(str(key));continue
    if n in['clv_segment_summary','clv_cohort_analysis']and col in['avg_clv_3_year','avg_clv_3_year_npv']and not coherent:holes.append(str(key));continue
    opts=aggregate_options.get((n,key,col),[w[key][col]for w in want]);v=row.get(col)
    if not any(equal(v,x)for x in opts):bad.append({'key':str(key),'actual':str(v),'independent_options':list(dict.fromkeys(map(str,opts)))})
    elif any(not equal(opts[0],x)for x in opts[1:]):variable.append(str(key))
   checks[n+'.'+col]=ck('fail'if bad else'gap'if holes or variable else'pass','Independent exact source arithmetic; explicit sequential rounding and unresolved lifecycle fallbacks kept separate',mismatch_count=len(bad),mismatches=bad[:8],undefined_rule_cells=holes,halfway_policy_cells=variable)
  precision=[]
  for row in rows:
   for col in cols:
    if col in TEXT|COUNTS|BOOLS|TIMES or col=='expected_next_order_date'or row.get(col)is None:continue
    v=row[col];places=1 if col in ONE else 2
    if type(v)in[D,int,float]and D(str(v))!=D(str(v)).quantize(D(1).scaleb(-places)):precision.append({'column':col,'actual':str(v),'places':places})
  checks[n+'.precision']=ck('fail'if precision else'pass','Explicit per-column decimal places; intermediate floating calculations do not relax declared final precision',errors=precision[:8],error_count=len(precision))
  seq=[tuple(row.get(c)for c in(['avg_clv_3_year']if n=='clv_segment_summary'else KEYS[n]))for row in rows]
  try:ok=all(a>=b for a,b in zip(seq,seq[1:]))if n=='clv_segment_summary'else all(a<=b for a,b in zip(seq,seq[1:]));checks[n+'.ordering']=ck('pass'if ok else'fail','Actual unsorted output order obeys explicitly requested keys; no arbitrary tie order')
  except TypeError:checks[n+'.ordering']=ck('fail','Required ordering values invalid')
 checks['policy_scope']=ck('gap'if undefined or any(variants[0]['outputs'][n]!=variants[1]['outputs'][n]for n in variants[0]['outputs'])else'pass','Actual original input enumerates unmatched lifecycle predicates and sequential nearest-rounding alternatives; no unique fallback invented',undefined_customer_ids=sorted(undefined),rounding_policies=[v['policy']for v in variants]);checks['workflow']=workflow or ck('pending','Actual seven-model build/source evidence missing');ss=[v['status']for v in checks.values()];reward=0 if'fail'in ss else None if'pending'in ss or'gap'in ss else 1
 return {'status':'unknown'if reward is None else'scored','reward':reward,'checks':checks,'model_requests':0,'native_rewards_modified':False}
