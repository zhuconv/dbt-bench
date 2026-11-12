"""Conservative original-input product audit with explicit unresolved boundaries."""
import argparse
from collections import Counter,defaultdict
from dataclasses import replace
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING
import hashlib,importlib.util,json
from pathlib import Path
import sys

HERE=Path(__file__).parent;TASK='deb__'+HERE.parent.name
MODELS={'staging':'stg_order_lines__products','intermediate':'int_product_sales_summary','mart':'product_performance'}
MODEL_PATHS={'staging':'models/staging/stg_order_lines__products.sql','intermediate':'models/intermediate/int_product_sales_summary.sql','mart':'models/marts/product_performance.sql'}
D=Decimal

def module(name):
    s=importlib.util.spec_from_file_location(TASK+'_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def expected_sets(derived):
    return {'staging':[dict(zip(derived['staging_columns'],r))for r in derived['staging']],
            'intermediate':[dict(zip(derived['intermediate_columns'],r))for r in derived['intermediate']],
            'mart':{k:[dict(zip(derived['mart_columns'],r))for r in v]for k,v in derived['mart_policy_candidates'].items()}}

def half_cent(value):
    scaled=value*100;lower=scaled.to_integral_value(rounding=ROUND_FLOOR)
    return scaled-lower==D('.5')

def normalize(table,expected,role,derived,exacts,policy):
    """Only known name modes and mathematically exact half-cent ties vary.

    Undefined zero-gross dependent values are masked only in value comparison;
    their actual types/enums/ranges are still checked and final audit is unknown.
    """
    rows=[dict(r)for r in table.rows];gold={r['product_id']:r for r in expected}if role!='staging'else{}
    modes={v['product_id']:set(v['choices'])for v in derived['profile']['name_mode_ties']}
    zero=set(derived['profile']['zero_gross']);notes=[]
    for row in rows:
        key=row.get('product_id')
        if role=='staging' or key not in gold:continue
        if key in modes and row.get('product_name')in modes[key]:row['product_name']=gold[key]['product_name']
        for name,value in exacts[role].get(key,{}).items():
            candidate=row.get(name)
            if value is not None and candidate is not None and type(candidate)in(int,float,D)and D(str(candidate)).is_finite()and half_cent(value):
                bounds={value.quantize(D('.01'),rounding=ROUND_FLOOR),value.quantize(D('.01'),rounding=ROUND_CEILING)}
                if D(str(candidate))in bounds:
                    if D(str(candidate))!=D(str(gold[key][name])):notes.append({'product_id':key,'column':name,'actual':str(candidate),'exact':str(value)})
                    row[name]=gold[key][name]
        if role=='mart'and key in zero:
            for name in('discount_intensity','margin_indicator','product_health_score','performance_tier'):
                if name in row:row[name]=gold[key][name]
    return replace(table,rows=rows),notes

def run(request,root):
    sys.path[:0]=[str(root/'scripts'),str(root/'src')]
    from audit_tables import load_observer_table,audit_table,TablePolicy,decode
    from dbt_bench.comparison import Column,TableContract
    workflow=module('workflow');derive=module('derive_expected');policy=json.loads((HERE/'policy.json').read_text());inputs=json.loads(workflow.read_ref(policy['independent_inputs'],root))
    if request['task_key']!=TASK or request.get('initial_database_sha256')!=inputs['initial_database_sha256']:raise ValueError('Task/raw input binding mismatch')
    derived=derive.derive(inputs,decode);expected=expected_sets(derived);parts={};observed={};exacts={'intermediate':{},'mart':{}}
    groups=defaultdict(list)
    for row in expected['staging']:groups[row['product_id']].append(row)
    for key,rows in groups.items():
        n=len({r['order_id']for r in rows});gross=sum((r['line_total']for r in rows),D(0));disc=sum((r['discount_amount']for r in rows),D(0));sold=sum((r['quantity_ordered']for r in rows),D(0));returned=sum((r['quantity_returned']for r in rows),D(0))
        exacts['intermediate'][key]={'gross_revenue':gross,'total_discounts':disc,'net_revenue':gross-disc,'avg_unit_price':sum((r['unit_price']for r in rows),D(0))/len(rows),'avg_order_quantity':sold/n}
        exacts['mart'][key]={'avg_revenue_per_order':derive.rnd(gross-disc)/n,'return_rate':returned/sold*100 if sold else None,'discount_intensity':derive.rnd(disc)/derive.rnd(gross)*100 if gross else None}
    text={'order_line_id','order_id','product_id','product_name','abc_class','velocity_class','margin_indicator','price_position','performance_tier'};integers={'total_orders','revenue_rank','order_frequency_rank','product_health_score'}
    precise_i={'gross_revenue':'DECIMAL(12,2)','total_discounts':'DECIMAL(12,2)','net_revenue':'DECIMAL(12,2)','avg_unit_price':'DECIMAL(10,2)','avg_order_quantity':'DECIMAL(8,2)'}
    precise_m={'total_units_sold':'DECIMAL(10,2)','total_units_returned':'DECIMAL(10,2)','gross_revenue':'DECIMAL(12,2)','net_revenue':'DECIMAL(12,2)','return_rate':'DECIMAL(5,2)','avg_revenue_per_order':'DECIMAL(10,2)','revenue_contribution_pct':'DECIMAL(5,2)','cumulative_revenue_pct':'DECIMAL(5,2)','discount_intensity':'DECIMAL(5,2)'}
    for role,model in MODELS.items():
        try:
            table=load_observer_table(request['snapshot'],task_key=TASK,database='/app/database/retail.duckdb',relation='main_product_analytics.'+model,stage=request['stage'],root=root)
            if len({n.casefold()for n in table.columns})!=len(table.columns):raise ValueError('Ambiguous physical column case')
            table=replace(table,columns=tuple(n.casefold()for n in table.columns),physical_columns=tuple(n.casefold()for n in table.physical_columns),sql_types={n.casefold():v for n,v in table.sql_types.items()},physical_sql_types={n.casefold():v for n,v in table.physical_sql_types.items()},rows=[{n.casefold():v for n,v in r.items()}for r in table.rows]);observed[role]=table
            cols=derived['staging_columns']if role=='staging'else derived['intermediate_columns']if role=='intermediate'else derived['mart_columns'];types={};columns=[]
            for name in cols:
                kind='text'if name in text else'integer'if name in integers else'date'if name=='order_date'else'numeric';columns.append(Column(name,kind));actual=table.physical_sql_types.get(name,'__missing__')
                target='VARCHAR'if kind=='text'else'INTEGER'if kind=='integer'else'DATE'if kind=='date'else(precise_i if role=='intermediate'else precise_m if role=='mart'else{}).get(name)
                types[name]=(target,)if target else(actual,)if actual.startswith('DECIMAL(')else('__required_decimal__',)
            if any(type(v)in(float,D)and not D(str(v)).is_finite()for r in table.rows for v in r.values()):raise ArithmeticError('Nonfinite actual numeric value')
            contract=TableContract(tuple(columns),keys=('order_line_id',)if role=='staging'else('product_id',),allow_extra_columns=role!='mart')
            declared=TablePolicy(contract,types,not_null=('order_line_id',)if role=='staging'else('product_id',),relation_types=('BASE TABLE',)if role=='mart'else('BASE TABLE','VIEW'))
            alternatives=expected['mart']if role=='mart'else{'fixed':expected[role]};comparisons={};notes={}
            for name,gold in alternatives.items():
                normalized,ties=normalize(table,gold,role,derived,exacts,name)if role!='staging'else(table,[]);comparisons[name]=audit_table(normalized,declared,gold);notes[name]=ties
            good=[name for name,r in comparisons.items()if r['reward']==1.0];component=comparisons[good[0]]if good else next(iter(comparisons.values()));component['policy_results']={name:r['reward']for name,r in comparisons.items()};component['half_cent_observations']=notes
            if role=='mart':
                checks={'column_order':table.physical_columns==tuple(cols)}
                enums={'abc_class':{'A','B','C'},'velocity_class':{'Fast Mover','Good Seller','Moderate','Slow Mover','Stagnant'},'margin_indicator':{'Healthy','Moderate','Aggressive','Deep Discount'},'price_position':{'Premium','Above Average','Average','Below Average','Budget'},'performance_tier':{'Star Performer','Strong Performer','Average Performer','Underperformer','At Risk'}}
                checks['enums']=all(r.get(n)in allowed for r in table.rows for n,allowed in enums.items());checks['health_range']=all(type(r.get('product_health_score'))is int and 5<=r['product_health_score']<=100 for r in table.rows)
                revenue=[r.get('net_revenue')for r in table.rows];checks['row_order']=all(type(v)in(int,float,D)for v in revenue)and all(a>=b for a,b in zip(revenue,revenue[1:]));component['checks'].update(checks)
                if not all(checks.values()):component.update(status='scored',reward=0.0)
            parts[role]=component
        except ArithmeticError as exc:parts[role]={'status':'scored','reward':0.0,'reason':str(exc)}
        except Exception as exc:parts[role]={'status':'unknown','reward':None,'reason':str(exc)}
        try:parts['model:'+role]=workflow.workflow_check(request['workflow'],request['snapshot'],TASK,root,model,'main_product_analytics',MODEL_PATHS[role],role=='mart')
        except Exception as exc:parts['model:'+role]={'status':'unknown','reward':None,'reason':str(exc)}
    try:parts['project_workflow']=module('project_workflow').evaluate(request,root,MODELS)
    except Exception as exc:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(exc)}
    try:
        proof=request['idempotency'];checks={}
        if not proof.get('hook_finished_before_second_run'):raise ValueError('First capture ordering was not established')
        for role,model in MODELS.items():
            first=load_observer_table(proof['first_snapshot'],task_key=TASK,database='/app/database/retail.duckdb',relation='main_product_analytics.'+model,stage=proof['first_stage'],root=root);second=observed[role]
            first=replace(first,columns=tuple(n.casefold()for n in first.columns),physical_columns=tuple(n.casefold()for n in first.physical_columns),sql_types={n.casefold():v for n,v in first.sql_types.items()},physical_sql_types={n.casefold():v for n,v in first.physical_sql_types.items()},rows=[{n.casefold():v for n,v in r.items()}for r in first.rows])
            def bag(t):return Counter(tuple((n,type(v).__name__,v)for n,v in sorted(r.items()))for r in t.rows)
            checks[role]=bag(first)==bag(second)and first.physical_sql_types==second.physical_sql_types and first.physical_columns==second.physical_columns
            if role=='mart':checks['mart_identical_ordered_rows']=first.rows==second.rows
        parts['idempotency']={'status':'scored','reward':float(all(checks.values())),'checks':checks,'reason':'Actual typed first output captured before actual second build; installed source held fixed'}
    except Exception as exc:parts['idempotency']={'status':'unknown','reward':None,'reason':str(exc)}
    parts['remaining_contract']={'status':'unknown','reward':None,'reason':'Active original data has 150 velocity differences between the two retained NTILE direction readings, exact half-cent rounding boundaries, and five zero-gross discount denominators without an explicit convention. Definite values/types/enums and workflow are checked, but no whole legal solution is certified.','zero_gross_ids':derived['profile']['zero_gross'],'scope':'Only zero-gross discount/margin/health/tier value comparison is deferred; their declared SQL types, enumerations and score bounds remain checked.'}
    rewards=[v['reward']for v in parts.values()];reward=0.0 if 0.0 in rewards else None
    files=[HERE/n for n in('check.py','workflow.py','project_workflow.py','derive_expected.py','policy.json')]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py']
    return {'schema_version':1,'target':'audit','task_key':TASK,'status':'scored'if reward==0 else'unknown','reward':reward,'components':parts,'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in files],'scope':'Explicit SQL decimal/integer declarations, three original-input relations, ordering/idempotency and trusted workflow; unresolved formula boundaries are separate and never widened from native reward1.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
    try:result=run(json.loads(a.request.read_text()),a.repo_root)
    except Exception as exc:result={'target':'audit','status':'unknown','reward':None,'reason':str(exc)}
    a.output.write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':main()
