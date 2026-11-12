"""Conservative independent inventory audit; unresolved enrichment stays unknown."""
import argparse
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).parent
TASK = 'ade__' + HERE.parent.name
MODELS = ('dim_products', 'fact_inventory', 'obt_product_inventory') if TASK.endswith('006') else ('obt_product_inventory',)
INT_TYPES = ('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')


def module(name):
    spec = importlib.util.spec_from_file_location(TASK+'_'+name, HERE/(name+'.py'))
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def integral(value, bounds):
    if value is None: return None
    if type(value) not in (int, float, Decimal): raise ValueError('Non-numeric integer representation')
    if type(value) is float and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError('Nonfinite or fractional integer representation')
    if type(value) is Decimal and (not value.is_finite() or value != value.to_integral_value()):
        raise ValueError('Nonfinite or fractional integer representation')
    if value < bounds[0] or value > bounds[1]: raise ValueError('Integer outside original input bounds')
    return int(value)


def run(request, root):
    sys.path[:0] = [str(root/'scripts'), str(root/'src')]
    from audit_tables import TablePolicy, load_observer_table, audit_table
    from dbt_bench.comparison import Column, TableContract
    workflow, derive = module('workflow'), module('derive_expected')
    if request['task_key'] != TASK: raise ValueError('Wrong task')
    policy = json.loads((HERE/'policy.json').read_text())
    inputs = json.loads(workflow.read_ref(policy['independent_inputs'], root))
    if request.get('initial_database_sha256') != inputs['initial_database_sha256']:
        raise ValueError('Original current-image input binding not established')
    expected = derive.derive(inputs)
    parts = {}
    for model in MODELS:
        try:
            candidate = load_observer_table(request['snapshot'], task_key=TASK,
                database='/app/analytics_engineering.duckdb', relation='main.'+model,
                stage=request['stage'], root=root)
            names = {n: n.casefold() for n in candidate.physical_columns}
            if len(set(names.values())) != len(names): raise ValueError('Ambiguous casefold columns')
            # The original hidden ipd alias is not a contract requirement.
            # A single product_id is sufficient; clearer inventory aliases also work.
            original = {n.casefold(): n for n in candidate.physical_columns}
            if model == 'obt_product_inventory':
                aliases = [n for n in ('inventory_product_id','ipd') if n in original]
                if aliases:
                    names[original[aliases[0]]] = 'product_id'
                    if 'product_id' in original: names[original['product_id']] = '_product_block_id'
            rows = [{names[n]: v for n,v in row.items()} for row in candidate.rows]
            candidate = replace(candidate, columns=tuple(names[n] for n in candidate.columns),
                physical_columns=tuple(names[n] for n in candidate.physical_columns),
                sql_types={names[n]: v for n,v in candidate.sql_types.items()},
                physical_sql_types={names[n]: v for n,v in candidate.physical_sql_types.items()}, rows=rows)
            expected_rows = expected[model]
            if model != 'fact_inventory':
                raw_products = {r['id']:r for r in inputs['relations']['products']['rows']}
                expected_rows = [dict(r) for r in expected_rows]
                for extra in ('supplier_ids','attachments','_product_block_id'):
                    if extra in candidate.physical_columns:
                        for row in expected_rows:
                            row[extra] = row['product_id'] if extra == '_product_block_id' else raw_products[row['product_id']][extra]
                if 'supplier_company' in candidate.physical_columns:
                    suppliers = {r['id']:r['company'] for r in inputs['relations']['suppliers']['rows']}
                    for row in candidate.rows:
                        key = row.get('product_id')
                        if key in raw_products:
                            ids = raw_products[key]['supplier_ids']
                            if ids and ';' not in ids and row['supplier_company'] != suppliers[int(ids)]:
                                raise ArithmeticError('Incorrect company for an unambiguous single supplier')
            columns = tuple(expected_rows[0])
            if not set(columns) <= set(candidate.physical_columns):
                raise ValueError('Unrecognized/missing attribute naming requires review; no hidden SQL alias whitelist')
            kinds, types = {}, {}
            for name in columns:
                values = [r[name] for r in expected_rows if r[name] is not None]
                kind = 'exact' if not values else 'integer' if type(values[0]) is int else 'numeric' if type(values[0]) is float else 'date' if type(values[0]) is date else 'text'
                kinds[name] = kind
                # No explicit SQL widths are specified. Validate lossless values
                # rather than inheriting the reference's physical widths.
                types[name] = (candidate.physical_sql_types[name],)
                for row in candidate.rows:
                    value = row[name]
                    if kind == 'integer': row[name] = integral(value, (min(values),max(values)))
                    elif kind == 'numeric' and value is not None:
                        if type(value) not in (int,float,Decimal) or not Decimal(str(value)).is_finite():
                            raise ArithmeticError('Nonfinite/non-numeric product measure')
                    elif kind == 'date' and value is not None:
                        if type(value) is datetime: row[name] = value.date()
                        elif type(value) is str:
                            try: row[name] = date.fromisoformat(value)
                            except ValueError: row[name] = datetime.strptime(value, '%m/%d/%Y %H:%M:%S').date()
            key = 'product_id' if model == 'dim_products' else 'inventory_id'
            table_policy = TablePolicy(TableContract(tuple(Column(n,kinds[n]) for n in columns),
                keys=(key,), allow_extra_columns=True), sql_types=types, not_null=(key,), relation_types=('BASE TABLE','VIEW'))
            parts[model] = audit_table(candidate, table_policy, expected_rows)
        except (ArithmeticError, ValueError) as exc:
            # A concrete invalid scalar is wrong. Absent/ambiguous observation or
            # aliases remain unknown; they are not a certificate of correctness.
            wrong = isinstance(exc, ArithmeticError) or any(s in str(exc) for s in ('integer representation','Integer outside'))
            parts[model] = {'status':'scored' if wrong else 'unknown','reward':0.0 if wrong else None,'reason':str(exc)}
        except Exception as exc:
            parts[model] = {'status':'unknown','reward':None,'reason':str(exc)}
        try:
            parts['workflow:'+model] = workflow.workflow_check(request['workflow'], request['snapshot'], TASK, root, model)
        except Exception as exc:
            parts['workflow:'+model] = {'status':'unknown','reward':None,'reason':str(exc)}
    parts['remaining_contract'] = {'status':'unknown','reward':None,
        'reason':'Actual source/raw investigation finds five multi-supplier products (supplier_ids 2;6 or 3;4); instruction does not specify supplier-company aggregation/representation, NULL-only optional attributes, or inventory timestamp granularity. No complete legal witness is certified.',
        'scope':'Definite values, coverage, keys and trusted required-model workflow are checked independently above'}
    rewards = [part['reward'] for part in parts.values()]
    reward = 0.0 if 0.0 in rewards else None
    paths = [HERE/n for n in ('check.py','workflow.py','derive_expected.py','policy.json')]
    paths += [root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py']
    return {'schema_version':1,'task_key':TASK,'target':'audit','status':'scored' if reward==0 else 'unknown',
            'reward':reward,'components':parts,'evidence':request,
            'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
            'scope':'Original-input, definite field/grain audit and trusted dbt source/build; incomplete product-detail representation remains unknown'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
    try: result=run(json.loads(a.request.read_text()),a.repo_root)
    except Exception as exc: result={'target':'audit','status':'unknown','reward':None,'reason':str(exc)}
    a.output.write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':main()
