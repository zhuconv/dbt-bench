"""Original Northwind data recomputation, without reference output or SQL."""
from datetime import datetime

INVENTORY = ('inventory_id', 'transaction_type', 'transaction_created_date',
             'transaction_modified_date', 'product_id', 'quantity',
             'purchase_order_id', 'customer_order_id', 'comments')
PRODUCT = ('product_id', 'product_code', 'product_name', 'description',
           'standard_cost', 'list_price', 'reorder_level', 'target_level',
           'quantity_per_unit', 'discontinued', 'minimum_reorder_quantity', 'category')


def derive(inputs):
    raw = inputs['relations']
    products = {}
    for row in raw['products']['rows']:
        key = row['id']
        assert type(key) is int and key not in products
        products[key] = {name: key if name == 'product_id' else row[name]
                         for name in PRODUCT}
    inventory = {}
    originals = {}
    for row in raw['inventory_transactions']['rows']:
        key = row['id']
        assert type(key) is int
        if key in originals:
            assert row == originals[key], 'Non-identical repeated inventory ID is unresolved'
        originals[key] = row
        value = {name: key if name == 'inventory_id' else row[name]
                 for name in INVENTORY}
        # The supplied warehouse uses DATE; this deliberately verifies the
        # calendar date only, without certifying an unspecified time policy.
        value['transaction_created_date'] = datetime.strptime(
            row['transaction_created_date'], '%m/%d/%Y %H:%M:%S').date()
        inventory[key] = value
    obt = []
    for key, row in sorted(inventory.items()):
        assert row['product_id'] in products
        obt.append(dict(row, **{n: v for n, v in products[row['product_id']].items()
                                if n != 'product_id'}))
    staged = {r['id'] for r in raw['stg_products']['rows']}
    missing = [key for key, row in inventory.items() if row['product_id'] not in staged]
    return {'fact_inventory': list(inventory.values()), 'dim_products': list(products.values()),
            'obt_product_inventory': obt, 'raw_inventory_rows': len(raw['inventory_transactions']['rows']),
            'distinct_inventory_ids': len(inventory), 'raw_product_count': len(products),
            'warehouse_unmatched_inventory_ids': sorted(missing),
            'scope': 'All raw product attributes listed in PRODUCT and inventory values; created date calendar day only; supplier representation and NULL-only attachments not decided'}
