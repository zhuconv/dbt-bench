"""Three-way finite source-derived fields; preserve unresolved policy branches."""
from decimal import Decimal
from collections import Counter
import re
from audit_tables import audit_table,TablePolicy
from dbt_bench.comparison import Column,TableContract,compare_tables
from expected_v1 import COLS as FIELDS
NAME='three_way_match';COLS={NAME:FIELDS}
TEXT={'match_id','po_id','po_number','po_line_id','supplier_id','supplier_name','variant_id','sku','quantity_match_status','price_match_status','overall_match_status','exception_reasons','risk_category','recommended_action'}
COUNTS={'exception_count','days_to_invoice','days_receipt_to_invoice','receipt_count','invoice_count'}
TIMING={'days_to_invoice','days_receipt_to_invoice','risk_score','risk_category','overall_match_status','exception_reasons','exception_count','recommended_action'}
FRACTIONAL={'risk_score','risk_category','overall_match_status','recommended_action'}
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)

def evaluate(outputs,expected,workflow=None):
 o=outputs.get(NAME,{});t=o.get('table');rows=o.get('rows');checks={};profile=expected['profile'];policies={p:[dict(zip(FIELDS,row))for row in v['rows']]for p,v in expected['policies'].items()};maps={p:{r['po_line_id']:r for r in rs}for p,rs in policies.items()};want=policies['zero_reference'];multi={r['po_line_id']for r in profile['multiple_invoice_date_lines']};fractional={r['po_line_id']for r in profile['fractional_rejection_below_one']}
 if o.get('exists')is False:checks['presence']=ck('fail','Declared target absent')
 if o.get('missing_required_columns'):checks['columns']=ck('fail','Required fields absent')
 if t is None:checks['interface']=ck('pending','Actual physical SQLinterface unavailable')
 else:
  cols=[];types={}
  for c in FIELDS:
   kind='text'if c in TEXT else'integer'if c in COUNTS else'numeric';cols.append(Column(c,kind));typ=t.physical_sql_types.get(c,'');types[c]=('VARCHAR',)if c in TEXT else('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT')if c in COUNTS else(typ,)if re.fullmatch(r'DECIMAL\(\d+,\d+\)',typ)else('DECIMAL',)
  checks['columns']=ck('pass'if set(FIELDS)<=set(t.physical_columns)else'fail','Every required physical column retained; distinct from background DECIMAL-family failures')
  for c in FIELDS:
   if c not in TEXT|COUNTS:
    typ=t.physical_sql_types.get(c,'');checks[c+'.numeric_representation']=ck('pass'if re.fullmatch(r'DECIMAL\(\d+,\d+\)|DOUBLE|FLOAT|REAL|TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT',typ)else'fail','Numeric storage family target check, separate from stricter declared DECIMAL requirement',actual_type=typ)
  z=audit_table(t,TablePolicy(TableContract(tuple(cols),keys=('po_line_id',),allow_extra_columns=True),types));checks['interface']=ck('fail'if any(v is False for v in z.get('checks',{}).values())else'pass'if z['reward']==1 else'pending','37declared fields; VARCHAR/integer/DECIMAL logical SQLfamilies explicit, unspecified DECIMALscale not invented. Preserve-materialization baseline separately unresolved.',details=z)
 if rows is None:checks['values']=ck('pending','Actual complete row capture unavailable')
 else:
  pop=Counter(r.get('po_line_id')for r in rows);desired=Counter(r['po_line_id']for r in want);checks['population']=ck('pass'if pop==desired else'fail','Exactly invoice-joined POlines, including actual no-receipt rows; unique POline grain and multiplicity',actual_rows=len(rows),expected_rows=len(want));ids=[r.get('match_id')for r in rows];checks['match_id']=ck('pass'if len(set(ids))==len(ids)and all(isinstance(v,str)and v for v in ids)else'fail','Required unique VARCHAR match identifier; exact POline identifier text not imposed as only legal generation')
  for col in FIELDS:
   if col in ['match_id','po_line_id']:continue
   certain=[]
   for row in want:
    k=row['po_line_id'];vals=[m[k][col]for m in maps.values()];same=all(v==vals[0]for v in vals)
    if False:same=False
    if False:same=False
    if same:certain.append(row)
   selected={r['po_line_id']for r in certain};rs=[r for r in rows if r.get('po_line_id')in selected];kind='text'if col in TEXT else'integer'if col in COUNTS else'numeric';ct=TableContract((Column('po_line_id','text'),Column(col,kind,abs_tol=Decimal('1e-8')if kind=='numeric'else Decimal(0))),keys=('po_line_id',),allow_extra_columns=True);z=compare_tables(certain,rs,ct);checks[col]=ck('fail'if not z.equal else'gap'if len(certain)!=len(want)else'pass','Independently recomputed fields invariant across executed fallback branches; multi-date timing and fractional risk rows remain unscored.2decimal displayed percentages are retained as finite candidate representation, not universal rounding policy.',certain_rows=len(certain),unresolved_rows=len(want)-len(certain),comparison=z.__dict__)
 checks['policy_scope']=ck('pass','Current variant fixes zero receipt output metrics. Reviewed blind interpretation uses PO fallback only for internal quantity risk/block comparisons, earliest invoice date, continuous rejection tiers and four-place weighted price. Actual input ambiguities remain profiled, never hidden.',actual_profile=profile);checks['workflow']=workflow or ck('pending','Actual workflow not yet bound');ss=[v['status']for v in checks.values()];reward=0 if'fail'in ss else None if any(s in ['pending','gap']for s in ss)else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False,scope='Only independently certain finite source/formula fields; no unique policy or complete legal solution invented.')
