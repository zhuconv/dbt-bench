"""Recognize declared, active threshold predicates without deriving policy from output."""
from decimal import Decimal

def number(node, helper):
    node = helper.bare(node)
    if node.get('class') != 'CONSTANT':
        return None
    value = node['value']
    if value.get('is_null'):
        return None
    typ = value['type']
    if typ['id'] == 'DECIMAL':
        return Decimal(value['value']).scaleb(-typ['type_info']['scale'])
    if typ['id'] in ('INTEGER', 'BIGINT', 'HUGEINT', 'DOUBLE', 'FLOAT'):
        return Decimal(str(value['value']))
    return None

def conjunction(node):
    if node and node.get('type') == 'CONJUNCTION_AND':
        for child in node['children']:
            yield from conjunction(child)
    elif node:
        yield node

def inspect(parsed, helper):
    stages = helper.active_nodes(parsed['parsed_compiled_sql']['stg_basket__order_products']['ast'])
    unsupported = []
    for node in stages:
        if node.get('type') != 'SELECT_NODE':
            continue
        for key in ('having', 'qualify', 'sample'):
            if node.get(key):
                unsupported.append(key)
        for modifier in node.get('modifiers', []):
            if modifier.get('type') not in ('DISTINCT_MODIFIER', 'ORDER_MODIFIER'):
                unsupported.append(modifier.get('type'))
        for clause in conjunction(node.get('where_clause')):
            if helper.constant(clause) is True:
                continue
            if clause.get('type') == 'OPERATOR_IS_NOT_NULL' and helper.refs(clause) == {'product_id'}:
                continue
            unsupported.append('unreviewed staging predicate')
    predicates = {}
    ambiguous = []
    nodes = helper.active_nodes(parsed['parsed_compiled_sql']['fct_product_affinity_matrix']['ast'])
    for node in nodes:
        if node.get('type') != 'SELECT_NODE':
            continue
        for clause in conjunction(node.get('where_clause')):
            op = clause.get('type')
            if op not in ('COMPARE_GREATERTHANOREQUALTO', 'COMPARE_GREATERTHAN', 'COMPARE_LESSTHANOREQUALTO', 'COMPARE_LESSTHAN'):
                if helper.refs(clause) & {'support','confidence','lift','orders_with_both'}:
                    ambiguous.append('non-simple metric predicate')
                continue
            left, right = helper.bare(clause['left']), helper.bare(clause['right'])
            reverse = left.get('class') != 'COLUMN_REF' and right.get('class') == 'COLUMN_REF'
            col, val = (right, left) if reverse else (left, right)
            if col.get('class') != 'COLUMN_REF':
                continue
            name = col['column_names'][-1].lower()
            if name not in ('support','confidence','lift','orders_with_both'):
                continue
            limit = number(val, helper)
            lower = (op in ('COMPARE_LESSTHANOREQUALTO','COMPARE_LESSTHAN')) if reverse else (op in ('COMPARE_GREATERTHANOREQUALTO','COMPARE_GREATERTHAN'))
            if limit is None or not lower:
                ambiguous.append(name)
            else:
                predicates.setdefault(name, []).append({'value': str(limit), 'strict': op in ('COMPARE_GREATERTHAN','COMPARE_LESSTHAN')})
    missing = sorted({'support','confidence','lift','orders_with_both'} - predicates.keys())
    return {'stage_policy_known': not unsupported, 'unsupported_stage': unsupported,
            'thresholds': predicates, 'missing_thresholds': missing,
            'ambiguous_thresholds': ambiguous,
            'status': 'unknown' if ambiguous else 'scored',
            'reward': None if ambiguous else float(not missing)}

def satisfies(row, predicates):
    for name, rules in predicates.items():
        value = row.get(name)
        if value is None:
            return False
        value = Decimal(str(value))
        if not value.is_finite():
            return False
        for rule in rules:
            limit = Decimal(rule['value'])
            if value <= limit if rule['strict'] else value < limit:
                return False
    return True
