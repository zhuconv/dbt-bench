"""Independent raw-data staging, multimap joins and category arithmetic."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

D = Decimal
FINAL = ('category_id','category_name','category_level','total_orders','total_units_sold',
         'total_revenue','total_cost','gross_profit','profit_margin','avg_order_value')


def trim(value):
    return value.strip() if isinstance(value, str) else value


def rnd(value, places='.01'):
    return D(value).quantize(D(places), rounding=ROUND_HALF_UP)


def raw(inputs, name, decode):
    item = inputs['relations'][name]
    names = [v['name'].lower() for v in item['schema']]
    return [dict(zip(names, (decode(v) for v in row))) for row in item['rows']]


def derive(inputs, decode):
    orders = raw(inputs, 'main.orders', decode)
    lines = raw(inputs, 'ORDERS.ORDER_LINES', decode)
    products = raw(inputs, 'PRODUCT.PRODUCTS', decode)
    categories = raw(inputs, 'PRODUCT.PRODUCT_CATEGORIES', decode)
    stages = {
        'orders': [dict(order_id=trim(r['order_id']), customer_id=trim(r['customer_id']),
                       status=trim(r['status']).upper(), grand_total=r['grand_total'],
                       ordered_at=r['ordered_at']) for r in orders],
        'products': [dict(product_id=trim(r['product_id']), product_code=trim(r['product_code']),
                         product_name=trim(r['product_name']), category_id=trim(r['primary_category_id']),
                         cost_price=D(0) if r['cost_price'] is None else r['cost_price']) for r in products],
        'categories': [dict(category_id=trim(r['category_id']), category_code=trim(r['category_code']),
                           category_name=trim(r['category_name']), category_level=r['category_level'])
                       for r in categories],
        'lines': [dict(order_line_id=trim(r['order_line_id']), order_id=trim(r['order_id']),
                      product_id=trim(r['product_id']), quantity_ordered=r['quantity_ordered'],
                      unit_price=r['unit_price'], line_total=r['line_total'], status=trim(r['status']))
                  for r in lines]}
    if any(r['line_total'] is None or r['quantity_ordered'] is None for r in stages['lines']):
        raise ValueError('Unspecified NULL line amount/quantity requires separate contract review')
    maps = {}
    for name, key in [('orders','order_id'),('products','product_id'),('categories','category_id')]:
        mapping = defaultdict(list)
        for row in stages[name]: mapping[row[key]].append(row)
        maps[name] = mapping
    intermediate = []
    for line in stages['lines']:
        for order in maps['orders'][line['order_id']]:
            if order['status'] not in ('COMPLETED','DELIVERED','SHIPPED'): continue
            for product in maps['products'][line['product_id']]:
                for category in maps['categories'][product['category_id']]:
                    intermediate.append(dict(order_line_id=line['order_line_id'],order_id=line['order_id'],
                        product_id=line['product_id'],product_name=product['product_name'],
                        category_id=category['category_id'],category_name=category['category_name'],
                        category_level=category['category_level'],quantity_ordered=line['quantity_ordered'],
                        unit_price=line['unit_price'],revenue=line['line_total'],
                        cost=product['cost_price']*line['quantity_ordered']))
    groups = defaultdict(list)
    for row in intermediate:
        groups[(row['category_id'],row['category_name'],row['category_level'])].append(row)
    final, exact = [], {}
    for (key,name,level), rows in groups.items():
        orders_count = len({r['order_id'] for r in rows if r['order_id'] is not None})
        units = sum((r['quantity_ordered'] for r in rows),D(0))
        revenue = sum((r['revenue'] for r in rows),D(0)); cost = sum((r['cost'] for r in rows),D(0))
        profit = revenue-cost
        margin = profit/revenue if revenue else None
        aov = revenue/orders_count if orders_count else None
        final.append(dict(zip(FINAL,[key,name,level,orders_count,units,rnd(revenue),rnd(cost),
                                    rnd(profit),margin,rnd(aov) if aov is not None else None])))
        exact[key] = {'avg_order_value': aov, 'profit_margin': margin}
    return {'stages':stages,'intermediate':intermediate,'final':final,'exact':exact,
            'scope':'Original raw records only; explicit trim/NULL cost/status rules, independent multimap joins and Decimal sums/counts. No reference output or SQL used.'}
