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

# Independent extension, derived from instruction and initial public macros.
# No native test SQL is imported or executed.
from collections import defaultdict
from datetime import datetime, timedelta
import re

def dt(v):
    return None if v is None else datetime.fromisoformat(str(v))
def lt(a,b):
    return None if a is None or b is None else a<b
def gt(a,b):
    return None if a is None or b is None else a>b
def sql_and(a,b):
    return False if a is False or b is False else (None if a is None or b is None else True)
def frac(a,b):
    return None if a is None or b in (None,0) else float(a)/float(b)
def quantile(values,p):
    v=sorted(values)
    if not v:return None
    at=(len(v)-1)*p;lo=math.floor(at);hi=math.ceil(at)
    return v[lo]*(hi-at)+v[hi]*(at-lo) if hi!=lo else v[lo]
def full_derive(tables,pii_patterns):
    expected=derive(tables)
    orders=unique(tables[INPUTS[1]],'order_id'); variants=unique(tables[INPUTS[2]],'variant_id')
    products=unique(tables[INPUTS[3]],'product_id');categories=unique(tables[INPUTS[4]],'category_id')
    counts=Counter();totals=defaultdict(list);prices=defaultdict(list);rows={}
    for line in tables[INPUTS[0]]:
        k=line['order_line_id'];e=expected[k];o=orders.get(line['order_id'],{});s=line['status'];rows[k]=(line,o)
        counts[e['order_id'],line['product_id']]+=1
        value=Decimal(0) if s=='CREDIT' else e['line_total']
        if value is not None:totals[e['order_id']].append(value)
        if s not in ('CREDIT','RETURNED') and e['net_unit_price'] is not None:prices[line['product_id']].append(float(e['net_unit_price']))
    summaries={k:(quantile(v,.25),quantile(v,.75)) for k,v in prices.items()}
    ambiguities={'pending_null_quantity':[],'null_status_positive':[],'null_status_for_iqr':[],'category_trailing_separator':[]}
    for k,(l,o) in rows.items():
        e=expected[k];s=l['status'];q,ship,ret=[decimal(l[x]) for x in ('quantity_ordered','quantity_shipped','quantity_returned')]
        total=e['line_total'];profit=e['line_profit'];date=dt(o.get('ordered_at'))
        amount=abs(total) if s in ('CREDIT','RETURNED') and total is not None else total
        quantity=abs(q) if s in ('CREDIT','RETURNED') and q is not None else q
        e['dq_missing_required']=any(v is None or str(v).strip(' ')=='' for v in [e[c] for c in ('order_line_id','order_id','order_number','sku','product_name','product_category_leaf','unit_price','line_total','net_unit_price')]+[q])
        e['dq_revenue_flag']=('NEGATIVE_AMOUNT' if lt(amount,0) else 'ZERO_AMOUNT' if amount==0 else 'HIGH_AMOUNT' if gt(amount,10000) else 'ROUND_NUMBER_SUSPECT' if amount is not None and amount==amount.to_integral_value() and amount>100 and amount%100==0 else 'WEEKEND_HIGH_AMOUNT' if date and date.weekday() in (5,6) and gt(amount,5000) else None)
        e['dq_quantity_flag']=('NULL_QUANTITY' if quantity is None else 'NEGATIVE_QUANTITY' if quantity<0 else 'ZERO_QUANTITY' if quantity==0 else 'BULK_ORDER' if quantity>10000 else 'FRACTIONAL_QUANTITY' if quantity!=quantity.to_integral_value() else None)
        e['dq_duplicate_flag']='POTENTIAL_DUPLICATE' if counts[e['order_id'],l['product_id']]>1 else None
        e['dq_duplicate_suspected']=e['dq_duplicate_flag'] is not None
        e['dq_shipped_vs_ordered_valid']=not(ship is not None and q is not None and ship>q*Decimal('1.05'))
        e['dq_return_vs_shipped_valid']=not(s=='RETURNED' and ret is not None and ship is not None and ret>ship)
        if s=='PENDING':
            status=sql_and(None if ship is None else ship==0,None if ret is None else ret==0)
            if None in (ship,ret):ambiguities['pending_null_quantity'].append(k)
        elif s=='SHIPPED':status=gt(ship,0)
        elif s=='RETURNED':status=gt(ret,0)
        elif s=='CREDIT':status=sql_and(lt(q,0),lt(total,0))
        else:status=True
        e['dq_status_qty_valid']=status
        if status is None:ambiguities['null_status_positive'].append(k)
        if s is None:ambiguities['null_status_for_iqr'].append(k)
        p25,p75=summaries.get(l['product_id'],(None,None));net=None if s in ('CREDIT','RETURNED') or e['net_unit_price'] is None else float(e['net_unit_price'])
        e['dq_unit_price_iqr_flag']=('LOW_OUTLIER' if p25 is not None and lt(net,p25-1.5*(p75-p25)) else 'HIGH_OUTLIER' if p75 is not None and gt(net,p75+1.5*(p75-p25)) else None)
        grand=decimal(o.get('grand_total'));values=totals.get(e['order_id'],[]);summed=sum(values) if values else None
        e['dq_order_total_match']=not(grand is not None and summed is not None and abs(grand-summed)>Decimal('.01'))
        note=o.get('notes') if o.get('notes') is not None else o.get('analyst_notes')
        e['dq_pii_flag']=next((label for pattern,label in pii_patterns if note is not None and re.search(pattern,note)),None)
        e['dq_line_total_nonnegative']=s=='CREDIT' or total is None or total>=0
        rate=frac(e['discount_amount'],None if e['unit_price'] is None or q is None else e['unit_price']*q)
        e['dq_discount_rate_flag']=None if s in ('CREDIT','RETURNED') or rate is None else ('NEGATIVE_DISCOUNT' if rate<0 else 'DISCOUNT_EXCEEDS_PRICE' if rate>1 else 'HIGH_DISCOUNT' if rate>.7 else None)
        margin=frac(profit,total)
        e['dq_margin_flag']=None if s in ('CREDIT','RETURNED') else ('ZERO_REVENUE' if total==0 else 'NEGATIVE_MARGIN' if lt(profit,0) else 'EXCESS_MARGIN' if gt(margin,.8) else None)
        dates=[date,dt(l['created_at']),dt(l['updated_at'])]
        e['dq_line_dates_valid']=not any(a is not None and b is not None and a>b for a,b in zip(dates,dates[1:]))
        product=products.get(variants.get(l['variant_id'],{}).get('product_id'),{})
        category=categories.get(product.get('primary_category_id'),{})
        e['dq_category_missing']=product.get('primary_category_id') is None or not category or normalized(category.get('category_name')) is None
        if (category.get('category_path') or '').rstrip(' ').endswith('>'):ambiguities['category_trailing_separator'].append(k)
        e['dq_late_arriving']=s not in ('CREDIT','RETURNED') and dates[2] is not None and date is not None and dates[2]>date+timedelta(days=2)
        e['dq_is_valid']=(not e['dq_missing_required'] and all(e[c] is True for c in ('dq_line_total_nonnegative','dq_shipped_vs_ordered_valid','dq_return_vs_shipped_valid','dq_status_qty_valid','dq_order_total_match','dq_line_dates_valid')) and not e['dq_category_missing'] and not e['dq_duplicate_suspected'] and all(e[c] is None for c in ('dq_revenue_flag','dq_quantity_flag','dq_unit_price_iqr_flag','dq_pii_flag','dq_discount_rate_flag','dq_margin_flag')))
    return expected,ambiguities

def parse_patterns(text):
    block=text.split('{% macro dq_flag_potential_pii')[1].split('{% endmacro %}')[0]
    return re.findall(r"REGEXP_LIKE\(\{\{ text_col \}\}, '([^']*)'\) THEN '([^']*)'",block)

def unpack(value):
    if isinstance(value,dict) and '$type' in value:
        return Decimal(value['value']) if value['$type']=='decimal' else float(value['value']) if value['$type']=='float' else value['value']
    return value

if __name__=='__main__':
    import argparse,hashlib
    from pathlib import Path
    ap=argparse.ArgumentParser();ap.add_argument('--initial',required=True);ap.add_argument('--snapshot',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
    initial=json.loads(Path(args.initial).read_text());tables={k:v['rows'] for k,v in initial['inputs'].items()}
    patterns=parse_patterns(initial['files']['macros/data_quality/dq_checks.sql']['text']);assert len(patterns)==4
    expected,ambiguities=full_derive(tables,patterns)
    snapshot=json.loads(Path(args.snapshot).read_text());out=snapshot['outputs'][0];cols=[c['name'].lower() for c in out['schema']]
    output_path=Path(args.snapshot).parent/out['file'];rows=[dict(zip(cols,map(unpack,json.loads(line)))) for line in output_path.read_text().splitlines()]
    comparison=compare(expected,{'rows':rows,'columns':cols});comparison['scope']='All 33 columns, unique original input keys, preserving exact Decimal product/sum and pinned DOUBLE division/rounding. Flags independently implemented in Python from public macros and instruction.'
    def ref(path):return {'path':str(Path(path).absolute()),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    result={'schema_version':1,'task_key':'deb__dbt-dq-macro-enforcement','comparison':comparison,'ambiguity_queries':ambiguities,'literal_pii_patterns':patterns,'expected_flag_counts':{c:dict(Counter(str(r[c]) for r in expected.values())) for c in cols if c.startswith('dq_')},'inputs':[ref(args.initial),ref(args.snapshot),ref(output_path),ref(__file__)],'tested_checker_invoked_to_define_validity':False}
    Path(args.output).write_text(json.dumps(result,default=str,indent=2)+'\n');print(json.dumps({'comparison':{k:v for k,v in comparison.items() if k!='mismatches'},'mismatch_count':len(comparison['mismatches']),'examples':comparison['mismatches'][:10],'ambiguities':{k:len(v) for k,v in ambiguities.items()}},default=str))
