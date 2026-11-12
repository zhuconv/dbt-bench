"""The phase4 instruction variant explicitly names all inventory output fields."""
REQUIRED = frozenset(('warehouse_id', 'warehouse_name', 'warehouse_type', 'city',
    'state_province', 'total_quantity', 'inventory_value', 'distinct_product_count',
    'avg_unit_cost', 'max_single_item_value', 'inventory_concentration'))


def check(names):
    normalized = [n.casefold() for n in names]
    missing = sorted(REQUIRED - set(normalized))
    ambiguous = len(normalized) != len(set(normalized))
    return {'status': 'scored', 'reward': float(not missing and not ambiguous),
            'missing_names': missing, 'ambiguous_names': ambiguous,
            'reason': 'Variant explicitly requires snake_case metric labels and source identification names; DuckDB identifier case is insensitive, extra columns remain allowed.'}
