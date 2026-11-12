"""Scoped DQ recomputation from public inputs; never calls the native grader.

This checks grain, preserved columns, normalization, pricing and correction flags.
It intentionally does not certify all DQ macros or the full task.
"""
from collections import Counter
from decimal import Decimal
import json
import math

INPUTS = ('stg_orders__order_lines','stg_orders__orders','stg_product__product_variants',
          'stg_product__products','stg_product__product_categories')
PRESERVED = ('order_line_id','order_id','order_number','sku','variant_name','product_name',
             'unit_price','discount_amount','line_total','line_profit')
NUMERIC = {'unit_price','discount_amount','line_total','line_profit','net_unit_price'}


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),default=str,allow_nan=False)


def canonical_inputs(tables):
    return {name:sorted(tables[name],key=encoded) for name in INPUTS}


def decimal(value):
    return None if value is None else Decimal(str(value))


def normalized(value):
    return None if value is None or str(value).strip(' ')=='' else str(value).strip(' ')


def sql_net_price(total, quantity):
    """Pinned DuckDB 1.2.2 literal expression: DECIMAL / DECIMAL -> DOUBLE.

    Model binary division, scale by 100, then round ties away from zero.
    Only this explicitly floating expression is converted; keys and Decimal
    products/sums remain exact. See the retained source/runtime diagnosis.
    """
    if total is None or quantity in (None,0):return None
    value=float(total)/float(quantity)
    scaled=abs(value*100.0);whole=math.floor(scaled)
    return math.copysign(whole+int(scaled-whole>=0.5),value)/100.0


def unique(rows,key):
    result={}
    for row in rows:
        value=row[key]
        if value is None or value in result:raise ValueError('Null or duplicate input key: '+key)
        result[value]=row
    return result


def derive(tables):
    lines=unique(tables[INPUTS[0]],'order_line_id')
    orders=unique(tables[INPUTS[1]],'order_id')
    variants=unique(tables[INPUTS[2]],'variant_id')
    products=unique(tables[INPUTS[3]],'product_id')
    categories=unique(tables[INPUTS[4]],'category_id')
    expected={}
    for key,line in lines.items():
        order=orders.get(line.get('order_id'),{})
        variant=variants.get(line.get('variant_id'),{})
        product=products.get(variant.get('product_id'),{})
        category=categories.get(product.get('primary_category_id'),{})
        sku_primary,sku_fallback=normalized(variant.get('sku')),normalized(line.get('sku'))
        name_primary,name_fallback=normalized(product.get('product_name')),normalized(line.get('product_name'))
        name=name_primary if name_primary is not None else name_fallback
        path=normalized(category.get('category_path'))
        leaf=(path.rsplit('>',1)[-1].strip(' ') if path is not None else
              normalized(category.get('category_name')) or name)
        qty,price,discount,tax=[decimal(line.get(k)) for k in ('quantity_ordered','unit_price','discount_amount','tax_amount')]
        total=None if None in (qty,price,discount,tax) else qty*price-discount+tax
        profit=None if total is None or qty is None else total-qty*(decimal(variant.get('cost_price')) or Decimal(0))
        net_float=sql_net_price(total,qty)
        net=decimal(net_float)
        original_total=decimal(line.get('line_total'))
        corrected=total is not None and original_total is not None and abs(total-original_total)>Decimal('.01')
        net_corrected=net_float is not None and price is not None and abs(net_float-float(price))>0.01
        expected[key]={'order_line_id':key,'order_id':order.get('order_id'),'order_number':order.get('order_number'),
            'sku':sku_primary if sku_primary is not None else sku_fallback,'variant_name':variant.get('variant_name'),
            'product_name':name,'product_category_leaf':leaf,'unit_price':price,'discount_amount':discount,
            'line_total':total,'line_profit':profit,'net_unit_price':net,'line_total_corrected':corrected,
            'unit_price_net_corrected':net_corrected,
            'dq_format_corrected':corrected or net_corrected or (sku_primary is None and sku_fallback is not None)
                                  or (name_primary is None and name_fallback is not None)}
    return expected


def compare(expected,snapshot):
    rows=snapshot['rows'];columns={c.lower() for c in snapshot['columns']}
    counts=Counter(r.get('order_line_id') for r in rows)
    missing_cols=sorted(set(PRESERVED)-columns)
    required=set(PRESERVED)|{c for row in expected.values() for c in row}
    mismatches=[]
    for row in rows:
        key=row.get('order_line_id')
        if key not in expected:continue
        for column,value in expected[key].items():
            if column not in columns:continue
            actual=row[column]
            if column in NUMERIC:
                actual=decimal(actual)
                equal=actual==value
            else:
                equal=type(actual) is type(value) and actual==value
            if not equal:mismatches.append({'key':key,'column':column,'expected':value,'actual':actual})
    result={'missing_keys':sorted(set(expected)-set(counts)),
        'extra_keys':sorted(set(counts)-set(expected),key=str),
        'duplicate_keys':[{'key':k,'count':n} for k,n in counts.items() if n>1],
        'missing_preserved_columns':missing_cols,'missing_scoped_columns':sorted(required-columns),'mismatches':mismatches,
        'expected_rows':len(expected),'actual_rows':len(rows),
        'scope':'Only declared normalization/pricing/correction fields and source-key coverage; not full DQ semantics'}
    result['equal']=not any(result[k] for k in ('missing_keys','extra_keys','duplicate_keys','missing_preserved_columns','missing_scoped_columns','mismatches'))
    return result
