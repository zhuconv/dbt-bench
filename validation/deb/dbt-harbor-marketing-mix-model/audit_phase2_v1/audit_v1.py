"""Finite MMM source arithmetic and declared15-relation interface; uncertain decisions stay scoped."""
from pathlib import Path
from collections import Counter
from decimal import Decimal as D
from datetime import date,datetime,time
from dataclasses import replace
import json,re,math
from audit_tables import audit_table,TablePolicy
from dbt_bench.comparison import TableContract,Column,compare_tables
HERE=Path(__file__).parent
SPECS=json.loads((HERE/'table_specs_v1.json').read_text());RULES=json.loads((HERE/'rule_specs_v1.json').read_text());COLS={r['relation'].split('.')[-1]:r['columns']for r in SPECS};KEYS={r['relation'].split('.')[-1]:r['keys']for r in SPECS}
ALLOWED={'Email','Paid Search','Social Media','Display Ads','TV/Video'}
DATES={'metric_date','time_period'};IDS={'channel_id'};COUNTS={'order_count','units_sold','impressions','clicks','day_of_week','month','quarter','rank_by_effectiveness'};FLAGS={'is_weekend','is_holiday','is_oversaturated'};TEXT={'channel_name','channel_type','day_name','holiday_name','saturation_status','recommended_action','top_performing_channel','most_saturated_channel'}
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def day(x):return x.date()if isinstance(x,datetime)and x.time()==time()else x
def numeric(x):return type(x)in[int,float,D]and D(str(x)).is_finite()
def same(a,b,tol=D('0.000001')):
 if a is None or b is None:return a is b
 if numeric(a)and numeric(b):return abs(D(str(a))-D(str(b)))<=tol
 return type(a)is type(b)and a==b

def source_compare(rows,want,keys,columns):
 rows=[{c:day(v)if c in DATES else int(v)if c in FLAGS and type(v)is bool else v for c,v in r.items()}for r in rows];want=[{c:day(v)if c in DATES else int(v)if c in FLAGS and type(v)is bool else v for c,v in r.items()}for r in want];cs=[]
 for col in list(dict.fromkeys(keys+columns)):
  values=[r.get(col)for r in want if r.get(col)is not None];sample=values[0]if values else None;kind='date'if col in DATES else'text'if col in keys or isinstance(sample,str)else'integer'if col in COUNTS|FLAGS else'numeric';tol=D('0.000001')if any(type(v)is float for v in values)else D(0);cs.append(Column(col,kind,abs_tol=tol if kind=='numeric'else D(0)))
 z=compare_tables(want,rows,TableContract(tuple(cs),keys=tuple(keys),allow_extra_columns=True));return ck('pass'if z.equal else'fail','Input-only arithmetic; exact identifiers/counts/dates, only explicit transcendental float measures use abs1e-6',comparison=z.__dict__)

def evaluate(outputs,expected,workflow=None):
 exp=expected['expected'];facts=expected['facts'];checks={}
 for spec in SPECS:
  n=spec['relation'].split('.')[-1];o=outputs.get(n,{});t=o.get('table');rows=o.get('rows')
  if o.get('exists')is False:checks[n+'.presence']=ck('fail','Declared output absent')
  if o.get('missing_required_columns'):checks[n+'.columns']=ck('fail','Required actual output column absent')
  if t is None:checks[n+'.interface']=ck('pending','Actual exact SQL interface unavailable');continue
  cs=[];types={}
  for col in COLS[n]:
   typ=t.physical_sql_types.get(col,'');kind='text'if col in TEXT else'date'if col in DATES else'integer'if col in COUNTS else'exact'if col in IDS|FLAGS else'numeric';cs.append(Column(col,kind));types[col]=('VARCHAR',)if col in TEXT else('DATE','TIMESTAMP','TIMESTAMP WITH TIME ZONE')if col in DATES else('VARCHAR','INTEGER','BIGINT','HUGEINT')if col in IDS else('BOOLEAN','TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT')if col in FLAGS else('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT')if col in COUNTS else(typ,)if re.fullmatch(r'(DECIMAL\(\d+,\d+\)|DOUBLE|FLOAT|REAL|TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT)',typ)else('DECIMAL','DOUBLE')
  normalized=[{c:day(v)if c in DATES else int(v)if c in FLAGS and type(v)is bool else v for c,v in r.items()}for r in t.rows];ct=replace(t,rows=normalized);mat='BASE TABLE'if spec['relation'].startswith('main_marts.')else'VIEW';z=audit_table(ct,TablePolicy(TableContract(tuple(cs),keys=tuple(KEYS[n]),allow_extra_columns=True),types,relation_types=(mat,'TABLE')if mat=='BASE TABLE'else(mat,)));checks[n+'.interface']=ck('fail'if any(v is False for v in z.get('checks',{}).values())else'pass'if z['reward']==1 else'pending'if z['status']=='unknown'else'fail','All declared columns/grain/materialization; unspecified numeric SQL width not invented',details=z)
 for spec in RULES:
  rule=spec['mmm_rule'];n=spec['relation'].split('.')[-1];rows=outputs.get(n,{}).get('rows')
  if rows is None:checks[rule]=ck('pending','Actual required row capture unavailable');continue
  if spec.get('expected_group'):
   cols=spec['expected_columns'];want=exp[spec['expected_group']]
   if rule=='performance-sales-contribution':
    # Zero actual sales has no task-specified NULL-versus-zero contribution policy.
    holes=[r for r in want if r['contribution_to_total_sales_pct']is None];certainkeys={day(r['metric_date'])for r in want if r['contribution_to_total_sales_pct']is not None};want=[r for r in want if day(r['metric_date'])in certainkeys];subset=[r for r in rows if day(r['metric_date'])in certainkeys];z=source_compare(subset,want,spec['keys'],cols);z['zero_sales_policy_rows']=len(holes)
    if z['status']=='pass'and holes:z['status']='gap'
   else:z=source_compare(rows,want,spec['keys'],cols)
   checks[rule]=z;continue
  bad=[]
  for i,row in enumerate(rows):
   def test(c,w):
    if c not in row or not same(row[c],w):bad.append({'row':i,'column':c,'actual':str(row.get(c)),'expected':str(w)})
   try:
    if rule=='decomposition-accounting':
     total=sum(D(str(row[c]))for c in ['email_incremental','paid_search_incremental','social_incremental','display_incremental','tv_incremental']);test('total_incremental',total);test('model_predicted_sales',D(str(row['baseline_sales']))+total);test('residual',D(str(row['actual_sales']))-D(str(row['baseline_sales']))-total)
    elif rule=='decomposition-percentage-formulas':
     for c,numerator in [('baseline_pct','baseline_sales'),('email_pct','email_incremental'),('paid_search_pct','paid_search_incremental'),('social_pct','social_incremental'),('display_pct','display_incremental'),('tv_pct','tv_incremental'),('marketing_pct','total_marketing_incremental'),('residual_pct','residual')]:test(c,100*D(str(row[numerator]))/D(str(row['actual_sales']))if row['actual_sales']else None)
    elif rule=='weekly-ratios':
     for c,numerator,denom,mult in [('baseline_pct','baseline_sales','total_sales',100),('marketing_pct','total_marketing_incremental','total_sales',100),('overall_marketing_roas','total_marketing_incremental','total_marketing_spend',1)]:
      if row[denom]:test(c,mult*D(str(row[numerator]))/D(str(row[denom])))
    elif rule=='budget-channel-bounds':
     if not 20000<=row['recommended_budget']<=200000:bad.append({'row':i,'recommended_budget':str(row['recommended_budget'])})
    elif rule=='budget-change-accounting':
     delta=D(str(row['recommended_budget']))-D(str(row['current_budget']));test('budget_change',delta);test('budget_change_pct',100*delta/D(str(row['current_budget']))if row['current_budget']else None)
    elif rule=='overlap-date-range':
     if not facts['overlap'][0]<=day(row['metric_date'])<=facts['overlap'][1]:bad.append({'row':i,'date':str(row['metric_date'])})
    elif rule=='daily-channel-grain':pass
    else:raise ValueError('Unhandled declared rule '+rule)
   except(KeyError,TypeError,ArithmeticError,ValueError)as e:bad.append({'row':i,'error':str(e)})
  if rule=='daily-channel-grain':bad=[{'key':str(k),'count':v}for k,v in Counter((day(r.get('metric_date')),r.get('channel_name'))for r in rows).items()if v>1]
  checks[rule]=ck('fail'if bad else'pass','Explicit finite rule independently calculated; separate unknown decision columns not forced to reference values',error_count=len(bad),examples=bad[:8])
 # Clear original defects do not rely on incomplete decision definitions.
 n='int_mmm__baseline_sales';rows=outputs.get(n,{}).get('rows')
 if rows is not None:
  wanted={r['metric_date']:r for r in exp['baseline']};bad=[]
  for row in rows:
   w=wanted.get(day(row.get('metric_date')))
   if w and not any(same(row.get('baseline_revenue'),w[c])for c in ['baseline_revenue','baseline_revenue_overlap_low_spend']):bad.append({'date':str(row.get('metric_date')),'actual':str(row.get('baseline_revenue')),'all_history':w['baseline_revenue'],'overlap_history':w['baseline_revenue_overlap_low_spend']})
  checks['baseline-explicit-product']=ck('fail'if bad else'gap','Both actual low-spend history interpretations tested; matching one does not resolve40–60percent guidance tension',error_count=len(bad),examples=bad[:5])
 rows=outputs.get('stg_mmm__daily_sales',{}).get('rows')
 if rows is not None:checks['canonical-channel-domain']=ck('fail'if any(r.get('channel_name')not in ALLOWED for r in rows)else'pass','Exactly five allowed canonical names; unspecified alias mapping not uniquely invented',actual_names=sorted({str(r.get('channel_name'))for r in rows}))
 for n in ['rpt_mmm_channel_effectiveness','rpt_mmm_budget_optimization','rpt_mmm_saturation_analysis']:
  rows=outputs.get(n,{}).get('rows')
  if rows is not None:
   counts=Counter(r.get('channel_name')for r in rows);checks[n+'.per_channel_grain']=ck('fail'if any(v!=1 for v in counts.values())else'pass','Explicit one row per channel',counts=dict(counts))
 rows=outputs.get('rpt_mmm_budget_optimization',{}).get('rows')
 if rows is not None:
  total=sum(D(str(r['recommended_budget']))for r in rows);checks['fixed-monthly-budget']=ck('pass'if total==D(500000)else'fail','Explicit fixed500000 monthly budget',actual=str(total))
 rows=outputs.get('int_mmm__daily_decomposition',{}).get('rows')
 if rows is not None:checks['decomposition-preserves-channel-incremental']=source_compare(rows,exp['decomposition'],['metric_date'],['email_incremental','paid_search_incremental','social_incremental','display_incremental','tv_incremental'])
 checks['policy_scope']=expected.get('executed_policy_review',ck('pending','Eight actual report/policy comparisons not yet bound to current input'))
 checks['workflow']=workflow or ck('pending','Actual source/build action evidence unavailable');ss=[v['status']for v in checks.values()];reward=0 if'fail'in ss else None if any(s in ['pending','gap']for s in ss) else 1
 return {'status':'unknown'if reward is None else'scored','reward':reward,'checks':checks,'model_requests':0,'native_rewards_modified':False,'scope':'Explicit finite original-input scalar/group rules; whole model validity never inferred from original source or successful native reward.'}
