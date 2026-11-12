"""Inspect actual compiled payment SQL at the task-declared staging boundary."""
import hashlib
from source_inspection_v1 import walk, ck


def reachable(ast):
    if ast.get('error') or len(ast.get('statements', [])) != 1:
        raise ValueError('Expected a parsed single query')
    active, leaves, visiting = [], [], set()

    def query(q, inherited):
        if id(q) in visiting:
            raise ValueError('Recursive query requires separate review')
        visiting.add(id(q))
        env = dict(inherited)
        env.update({x['key'].lower(): x['value']['query']['node']
                    for x in q.get('cte_map', {}).get('map', [])})
        kind = q.get('type')
        if kind == 'SET_OPERATION_NODE':
            query(q['left'], env)
            query(q['right'], env)
        elif kind == 'SELECT_NODE':
            active.extend(q.get('select_list', []))

            def table(t):
                if not t:
                    return
                if t['type'] == 'BASE_TABLE':
                    name = t['table_name'].lower()
                    if not t.get('schema_name') and name in env:
                        query(env[name], env)
                    else:
                        leaves.append((t.get('schema_name', '').lower(), name))
                elif t['type'] == 'JOIN':
                    table(t.get('left'))
                    table(t.get('right'))
                elif t['type'] == 'SUBQUERY':
                    query(t['subquery']['node'], env)
                elif t['type'] != 'EMPTY':
                    raise ValueError('Unreviewed table form ' + t['type'])
            table(q.get('from_table'))
            # Scalar/EXISTS subqueries are part of actual query lineage too.
            for name, value in q.items():
                if name in ('cte_map', 'from_table'):
                    continue
                for child in walk(value):
                    if child.get('class') == 'SUBQUERY':
                        query(child['subquery']['node'], env)
        else:
            raise ValueError('Unreviewed query type ' + str(kind))
        visiting.remove(id(q))

    query(ast['statements'][0]['node'], {})
    return active, leaves


def inspect(records, manifest, files, changed_model_paths, staging_unchanged):
    name = 'supplier_payment_optimization'
    matches = [(uid, n) for uid, n in manifest['nodes'].items()
               if n.get('resource_type') == 'model' and n['name'] == name]
    assert len(matches) == 1
    uid, node = matches[0]
    parsed = [r for r in records if r['unique_id'] == uid]
    assert len(parsed) == 1
    parsed = parsed[0]
    assert parsed['compiled_sha256'] == hashlib.sha256(node['compiled_code'].encode()).hexdigest()
    assert files[node['original_file_path']].strip() == node['raw_code'].strip()
    active, leaves = reachable(parsed['ast'])
    permitted = {('main', 'stg_procurement__supplier_invoices'),
                 ('main', 'stg_procurement__suppliers'),
                 ('main', 'stg_procurement__purchase_orders'),
                 ('main', 'stg_finance__currency_exchange_rates')}
    required = permitted - {('main', 'stg_procurement__purchase_orders')}
    refs = set(leaves)
    source_ok = required <= refs <= permitted and staging_unchanged
    scoped = [v for v in active if str(v.get('alias', '')).lower() == 'cash_outflow_week']
    funcs = [v.get('function_name', '').lower() for x in scoped for v in walk(x) if v.get('function_name')]
    path = 'models/marts/' + name + '.sql'
    return {
        'source_lineage': ck('pass' if source_ok else 'fail',
            'Actual compiled mart reads declared frozen staging inputs; their complete original values remain unchanged.',
            actual_relations=sorted(refs)),
        'staging_refs': ck('pass' if required <= refs <= permitted else 'fail',
            'All required staging relations and no additional external input.'),
        'single_model': ck('pass' if set(changed_model_paths) == {path} else 'fail',
            'Only the comprehensive mart source changed; initial public staging models remain intact.',
            changed_model_paths=sorted(changed_model_paths)),
        'weekofyear': ck('pass' if 'weekofyear' in funcs else 'fail',
            'Actual reachable cash_outflow_week expression uses WEEKOFYEAR.', functions=funcs),
    }
