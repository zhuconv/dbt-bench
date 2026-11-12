"""Independent Python aggregation of original ORDERS inputs; no solution SQL."""
from collections import defaultdict
from decimal import Decimal, localcontext

def dec(value):return Decimal(0) if value is None else Decimal(str(value))
def clean(value):return value.strip() if isinstance(value,str) else value

def build(raw):
    groups=defaultdict(list)
    for row in raw['lines']:groups[clean(row['order_id'])].append(row)
    cancelled={clean(r['order_id']) for r in raw['cancellations'] if r['order_id'] is not None}
    result={n:[] for n in ('int_orders_enriched','mart_order_totals','mart_revenue_by_source','mart_variance_details')}
    sources=defaultdict(list);seen=set();missing=0
    for row in raw['orders']:
        oid=clean(row['order_id']);source=clean(row['order_source'])
        if oid is None or oid in seen:raise ValueError('Original header key is null or duplicated')
        seen.add(oid);lines=groups.get(oid,[]);missing+=not bool(lines)
        subtotal,grand,tax=(dec(row[k]) for k in ('subtotal','grand_total','tax_total'))
        line=sum((dec(x['line_total'])for x in lines),Decimal(0));ltax=sum((dec(x['tax_amount'])for x in lines),Decimal(0));count=len(lines)
        sv,tv=subtotal-line,tax-ltax;isc=int(oid in cancelled)
        result['int_orders_enriched'].append(dict(order_id=oid,order_source=source,is_cancelled=isc,subtotal=subtotal,grand_total=grand,tax_total=tax,line_subtotal=line,line_tax=ltax,line_count=count,has_lines=int(count>0)))
        result['mart_order_totals'].append(dict(order_id=oid,order_source=source,is_cancelled=isc,header_subtotal=subtotal,header_grand_total=grand,line_subtotal=line,line_count=count,subtotal_variance=sv,has_variance=int(sv!=0),header_tax=tax,line_tax=ltax,tax_variance=tv,has_tax_variance=int(tv!=0)))
        with localcontext() as ctx:
            ctx.prec=80
            if sv or tv:result['mart_variance_details'].append(dict(order_id=oid,order_source=source,header_subtotal=subtotal,line_subtotal=line,subtotal_variance=sv,subtotal_variance_pct=sv/subtotal*100 if subtotal else Decimal(0),header_tax=tax,line_tax=ltax,tax_variance=tv,tax_variance_pct=tv/tax*100 if tax else Decimal(0)))
        if not isc:sources[source].append((grand,sum((dec(x['quantity_ordered'])for x in lines),Decimal(0))))
    for source,rows in sources.items():
        revenue=sum((r[0]for r in rows),Decimal(0));items=sum((r[1]for r in rows),Decimal(0))
        with localcontext()as ctx:
            ctx.prec=80
            result['mart_revenue_by_source'].append(dict(order_source=source,order_count=len(rows),total_revenue=revenue,total_items=items,avg_order_value=revenue/len(rows)))
    return result,{'raw_counts':{k:len(v)for k,v in raw.items()},'orders':len(seen),'headers_without_lines':missing,'orphan_lines':sum(len(v)for k,v in groups.items()if k not in seen),'cancelled_headers':len(seen&cancelled),'scope':'All original input rows; exact Decimal sums and 80-digit quotient, no reference/grader/gold used. Staging workflow/lineage evaluated separately.'}
