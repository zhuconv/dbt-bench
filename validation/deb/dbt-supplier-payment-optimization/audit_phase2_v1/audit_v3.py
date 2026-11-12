"""Independent fixed-input payment audit; explicit nearest-cent math, no broad epsilon."""
from decimal import Decimal as D
from datetime import date
from fractions import Fraction
import math,re
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import COLS
NAME='supplier_payment_optimization'
TEXT={'invoice_id','supplier_id','supplier_name','currency_code','aging_bucket','payment_terms_code','discount_opportunity_status','discount_roi_tier','priority_tier','supplier_risk_category','payment_strategy'}
DATES={'invoice_date','due_date','discount_deadline','recommended_payment_date'}
COUNTS={'days_until_due','days_past_optimal','priority_rank','cash_outflow_week','working_capital_days'}
NUMERIC=set(COLS)-TEXT-DATES-COUNTS
MONETARY={'invoice_amount','amount_usd','potential_savings_usd','cumulative_outflow_usd','weekly_outflow_usd','float_benefit_usd'}
def ck(status,reason,**kw):return dict(status=status,reason=reason,**kw)
def frac(x):return Fraction(D(str(x)))
def nearest(x,places=2):
 y=Fraction(x)*10**places;q,r=divmod(y.numerator,y.denominator);ns=[q]if 2*r<y.denominator else[q+1]if 2*r>y.denominator else[q,q+1]
 return {D(n).scaleb(-places)for n in ns}
def finite(x):return type(x)in(int,float,D)and D(str(x)).is_finite()
def equal(a,e):
 if isinstance(e,(D,int))and not isinstance(e,bool):return finite(a)and D(str(a))==D(e)
 return type(a)is type(e)and a==e if isinstance(e,date)else a==e

def evaluate(outputs,expected,workflow=None):
 o=outputs.get(NAME,{});rows=o.get('rows');checks={}
 if o.get('missing_required_columns'):checks['required_columns']=ck('fail','Actual trusted declared-column read reports required columns absent',columns=o['missing_required_columns'])
 if o.get('exists')is False:checks['presence']=ck('fail','Required actual output relation absent')
 t=o.get('table')
 if t is not None:
  numeric_types=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT','DOUBLE','FLOAT');types={};scales=[]
  for c in COLS:
   actual=t.physical_sql_types.get(c,'')
   if c in TEXT:allowed=('VARCHAR',)
   elif c in DATES:allowed=('DATE',)
   elif c in COUNTS:allowed=numeric_types+(actual,)if re.fullmatch(r'DECIMAL\(\d+,0\)',actual)else numeric_types
   else:
    m=re.fullmatch(r'DECIMAL\((\d+),(\d+)\)',actual);allowed=(actual,)if m and int(m[2])>=2 else('DECIMAL(18,2)',)
    if m:scales.append(int(m[2]))
   types[c]=allowed
  policy=TablePolicy(TableContract(tuple(Column(c,'text'if c in TEXT else'date'if c in DATES else'numeric')for c in COLS),keys=('invoice_id',),allow_extra_columns=True),types,relation_types=('TABLE','BASE TABLE','VIEW'))
  result=audit_table(t,policy);checks['interface']=ck('pending'if result['status']=='unknown'else'pass'if result['reward']==1 else'fail','Required named columns, declared DATE and consistent decimal representation; output may be a table or view',details=result)
  checks['consistent_decimal_precision']=ck('pass'if len(scales)==len(NUMERIC)and len(set(scales))==1 else'fail','All monetary/score columns use a common decimal scale, at least2; dollar values separately round to2',observed_scales=scales)
 else:checks['interface']=ck('pending','Exact physical SQL schema unavailable')
 if rows is not None:
  want={p:{r[0]:dict(zip(COLS,r))for r in rs}for p,rs in expected['policies'].items()};got={r.get('invoice_id'):r for r in rows};keys=set(next(iter(want.values())));checks['population']=ck('pass'if len(got)==len(rows)and set(got)==keys else'fail','Every eligible source invoice with valid as-ofFX, once',missing=sorted(keys-set(got)),extra=sorted(set(got)-keys));details={p:[]for p in want};column_errors={c:[]for c in COLS};policypass={p:True for p in want}
  terms={r['invoice_id']:r for r in expected['term_cases']};exact={p:{r['invoice_id']:r for r in rs}for p,rs in expected['exact_values'].items()}
  for k in got.keys()&keys:
   row=got[k]
   for c in COLS:
    oks=[]
    for p,wm in want.items():
     e=wm[k][c];allowed={e}if not isinstance(e,(dict,list))else[];q=None
     if c=='supplier_risk_score':q=exact[p][k]['risk_exact']
     elif c=='payment_priority_score':q=exact[p][k]['priority_exact']
     elif c=='supplier_invoice_concentration':q=exact[p][k]['concentration_exact']
     elif c=='annualized_discount_roi':q=terms[k]['roi_exact']
     elif c=='potential_savings_usd'and wm[k]['discount_opportunity_status']=='Available':q=frac(wm[k]['amount_usd'])*frac(wm[k]['early_payment_discount_pct'])/100
     elif c=='float_benefit_usd':q=frac(wm[k]['amount_usd'])*Fraction(5,100)/365*wm[k]['working_capital_days']
     if q is not None:allowed=nearest(q)
     ok=any(equal(row.get(c),v)for v in allowed);oks.append(ok)
     if not ok:policypass[p]=False;details[p].append({'invoice_id':k,'column':c,'actual':str(row.get(c)),'allowed':[str(v)for v in allowed]})
    if not any(oks):column_errors[c].append({'invoice_id':k,'actual':str(row.get(c)),'expected_by_policy':{p:str(w[k][c])for p,w in want.items()}})
  for c,errors in column_errors.items():checks[c]=ck('fail'if errors else'pass','Independent original-input formula/identity for '+c,error_count=len(errors),examples=errors[:6])
  checks['coherent_concentration_policy']=ck('pass'if any(policypass.values())else'fail','A single complete raw-ratio or displayed-ratio policy must explain all fields, never arbitrary per-row tolerance',matching_policies=[p for p,v in policypass.items()if v],differences={p:v[:8]for p,v in details.items()})
  invalid=[r.get('invoice_id')for r in rows if any(not finite(r.get(c))or D(str(r[c]))!=D(str(r[c])).quantize(D('.01'))for c in NUMERIC)or any(not finite(r.get(c))or D(str(r[c]))!=D(str(r[c])).to_integral_value()for c in COUNTS)]
  checks['precision_and_integral_counts']=ck('fail'if invalid else'pass','All reported money/scores are finite nearest-cent values; day/rank/week values integral',examples=invalid[:8])
 else:checks['values']=ck('pending','Actual rows unavailable')
 if expected['profile']['future_invoice_count']:
  checks['definition_policy']=ck('gap','Actual original input has future-dated invoices; text states age/180 cap1 and0-100risk but does not determine whether/where to floor negative age contributions. Raw versus displayed concentration also affects actual risk/priority. Matching a retained policy is reference-relative only.',future_invoice_count=expected['profile']['future_invoice_count'],executed_policies=list(expected['policies']))
 if rows is not None:
  invalid=[r.get('invoice_id')for r in rows if any(not finite(r.get(c))or not 0<=D(str(r[c]))<=100 for c in ['supplier_risk_score','payment_priority_score'])]
  checks['score_range']=ck('fail'if invalid else'pass','Explicit risk and priority score0-100 bounds',examples=invalid[:8])
 checks['workflow']=workflow or ck('pending','Trusted actual source/workflow evidence not captured');states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if any(v in states for v in ['pending','gap'])else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
