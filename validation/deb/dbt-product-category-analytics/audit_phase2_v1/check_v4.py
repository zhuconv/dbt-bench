"""Independent category audit with separate trusted workflow requirements."""
import argparse
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import hashlib, importlib.util, json, math
from pathlib import Path
import sys

HERE=Path(__file__).parent
TASK='deb__'+HERE.parent.name
MODELS={'lines':'stg_order_lines','products':'stg_products','categories':'stg_categories',
        'orders':'stg_orders','intermediate':'int_product_sales','final':'fct_category_performance'}

def module(name):
    spec=importlib.util.spec_from_file_location(TASK+'_'+name,HERE/(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def run(request,root):
    sys.path[:0]=[str(root/'scripts'),str(root/'src')]
    from audit_tables import load_observer_table,audit_table,TablePolicy,decode
    from dbt_bench.comparison import Column,TableContract
    workflow=module('workflow');derive=module('derive_expected')
    if request['task_key']!=TASK:raise ValueError('Wrong task identity')
    policy=json.loads((HERE/'policy.json').read_text())
    inputs=json.loads(workflow.read_ref(policy['independent_inputs'],root))
    if request.get('initial_database_sha256')!=inputs['initial_database_sha256']:
        raise ValueError('Original input database binding is not established')
    expected=derive.derive(inputs,decode);parts={}
    models=request.get('category_stage_models',MODELS)
    if set(models)!=set(MODELS) or models['intermediate']!=MODELS['intermediate'] or models['final']!=MODELS['final']:
        raise ValueError('Incomplete declared category model roles')
    for role,model in models.items():
        rows=expected['stages'][role] if role in expected['stages'] else expected[role]
        try:
            table=load_observer_table(request['snapshot'],task_key=TASK,
                database='/app/database/retail.duckdb',relation='sales_analytics.'+model,
                stage=request['stage'],root=root)
            if len({v.casefold()for v in table.columns})!=len(table.columns):raise ValueError('Ambiguous column case')
            table=replace(table,columns=tuple(n.casefold()for n in table.columns),
                physical_columns=tuple(n.casefold()for n in table.physical_columns),
                sql_types={n.casefold():t for n,t in table.sql_types.items()},
                physical_sql_types={n.casefold():t for n,t in table.physical_sql_types.items()},
                rows=[{n.casefold():v for n,v in r.items()}for r in table.rows])
            # Staging schemas/row retention are not declared by the instruction.
            # Their required source roles and lineage are checked from the trusted
            # build. Product strings that are actually exposed must be trimmed.
            if role in expected['stages']:
                trimmed=role!='products' or all(not isinstance(v,str) or v==v.strip() for r in table.rows for v in r.values())
                parts[role]={'status':'scored','reward':float(trimmed),'checks':{'exposed_product_strings_trimmed':trimmed},'scope':'Four staging roles and source/ref workflow; unspecified staging column names, optional fields and early filtering are not constrained to the reference.'}
                try:parts['model:'+role]=workflow.workflow_check(request['workflow'],request['snapshot'],TASK,root,model,'sales_analytics',None,False)
                except Exception as exc:parts['model:'+role]={'status':'unknown','reward':None,'reason':str(exc)}
                continue
            if role=='intermediate':
                # These names are defaults for semantic roles, not mandatory SQL
                # aliases; a trusted capture may declare another bijective map.
                projection=request.get('category_intermediate_columns',{n:n for n in ('product_id','category_id','revenue','cost')})
                if set(projection)!={'product_id','category_id','revenue','cost'} or len(set(projection.values()))!=4:raise ValueError('Incomplete/ambiguous intermediate semantic mapping')
                if not set(projection.values())<=set(table.columns):raise ValueError('Intermediate revenue/cost/product/category semantic columns require an explicit mapping; names are not prescribed')
                if 'order_line_id' in table.columns and 'order_line_id' not in projection:
                    projection={**projection,'order_line_id':'order_line_id'}
                rows=[{n:r[n]for n in projection}for r in rows]
                table=replace(table,columns=tuple(projection),physical_columns=tuple(projection),sql_types={n:table.sql_types[v]for n,v in projection.items()},physical_sql_types={n:table.physical_sql_types[v]for n,v in projection.items()},rows=[{n:r[v]for n,v in projection.items()}for r in table.rows])
            names=tuple(rows[0]);types={};columns=[]
            for name in names:
                nonnull=[r[name]for r in rows if r[name]is not None]
                first=nonnull[0]if nonnull else None
                kind='text'if type(first)is str else'datetime'if type(first)is datetime else'date'if type(first)is date else'numeric'if type(first)in(int,float,Decimal)else'exact'
                columns.append(Column(name,kind));physical=table.physical_sql_types.get(name,'__missing__')
                numeric=physical in('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT','FLOAT','DOUBLE')or physical.startswith('DECIMAL(')
                types[name]=(physical,)if kind not in('numeric','text')or(kind=='numeric'and numeric)or(kind=='text'and physical=='VARCHAR')else('__unsupported_sql_type__',)
            for row in table.rows:
                if any(type(v)in(float,Decimal)and not Decimal(str(v)).is_finite()for v in row.values()):
                    raise ArithmeticError('Nonfinite submission value')
            key='category_id'if role in('categories','final')else'product_id'if role=='products'else'order_id'if role=='orders'else'order_line_id'
            unique=key in rows[0] and len({r[key]for r in rows})==len(rows)
            table_policy=TablePolicy(TableContract(tuple(columns),keys=(key,)if unique else(),allow_extra_columns=True,max_unkeyed_rows=10000),
                sql_types=types,not_null=names if role=='final'else(),relation_types=('BASE TABLE',)if role=='final'else('BASE TABLE','VIEW'))
            component=audit_table(table,table_policy,rows)
            if role=='final':
                no_nulls=all(v is not None for r in table.rows for v in r.values())
                quality=all(all(r.get(n)is not None and type(r[n])in(int,float,Decimal)and Decimal(str(r[n]))>=0 for n in ('total_revenue','total_cost','gross_profit','avg_order_value'))and r.get('profit_margin')is not None and type(r['profit_margin'])in(int,float,Decimal)and 0<=r['profit_margin']<=1 for r in table.rows)
                component['checks']['all_columns_no_null']=no_nulls;component['checks']['money_margin_bounds']=quality
                if not(no_nulls and quality):component.update(status='scored',reward=0.0)
            parts[role]=component
        except ArithmeticError as exc:parts[role]={'status':'scored','reward':0.0,'reason':str(exc)}
        except Exception as exc:parts[role]={'status':'unknown','reward':None,'reason':str(exc)}
        try:
            path='models/intermediate/int_product_sales.sql'if role=='intermediate'else'models/marts/fct_category_performance.sql'if role=='final'else None
            parts['model:'+role]=workflow.workflow_check(request['workflow'],request['snapshot'],TASK,root,model,'sales_analytics',path,role=='final')
        except Exception as exc:parts['model:'+role]={'status':'unknown','reward':None,'reason':str(exc)}
    try:parts['project_workflow']=module('project_workflow_v2').evaluate(request,root,models)
    except Exception as exc:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(exc)}
    rewards=[v['reward']for v in parts.values()];reward=0.0 if 0.0 in rewards else None if None in rewards else 1.0
    files=[HERE/n for n in('check_v4.py','workflow.py','project_workflow_v2.py','derive_expected.py','policy.json')]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py']
    return {'schema_version':1,'task_key':TASK,'target':'audit','status':'unknown'if reward is None else'scored','reward':reward,'components':parts,
        'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in files],'evidence':request,
        'scope':'Independent original raw final and intermediate semantic values; unspecified staging representation unconstrained, table/grain and actual trusted model/project/source/backend workflow; no SQL implementation whitelist or native reward inference'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
    try:result=run(json.loads(a.request.read_text()),a.repo_root)
    except Exception as exc:result={'target':'audit','status':'unknown','reward':None,'reason':str(exc)}
    a.output.write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':main()
