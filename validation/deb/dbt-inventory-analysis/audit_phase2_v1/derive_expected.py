"""Original inventory rows -> independent warehouse aggregation."""
from collections import defaultdict
from decimal import Decimal,ROUND_HALF_UP
D=Decimal

def trim(v):return v.strip()if isinstance(v,str)else v
def rnd(v,scale='.01'):return D(v).quantize(D(scale),rounding=ROUND_HALF_UP)
def records(inputs,table,decode):
 item=inputs['relations'][table];names=[v['name'].casefold()for v in item['schema']];return[dict(zip(names,map(decode,row)))for row in item['rows']]
def derive(inputs,decode):
 levels=records(inputs,'INVENTORY.INVENTORY_LEVELS',decode);warehouses=records(inputs,'INVENTORY.WAREHOUSES',decode);cleanlevels=[{k:(D(0)if v is None else v)if k in('quantity_on_hand','quantity_available','quantity_reserved','unit_cost')else trim(v)for k,v in r.items()}for r in levels];cleanwarehouses=[{k:trim(v)for k,v in r.items()}for r in warehouses];lookup=defaultdict(list)
 for w in cleanwarehouses:lookup[w['warehouse_id']].append(w)
 groups=defaultdict(list)
 for line in cleanlevels:
  if line['quantity_on_hand']>0:
   for wh in lookup[line['warehouse_id']]:groups[tuple(wh.items())].append(line)
 final=[];exact={}
 for wh,rows in groups.items():
  qty=sum((r['quantity_on_hand']for r in rows),D(0));value=sum((r['quantity_on_hand']*r['unit_cost']for r in rows),D(0));maximum=max(r['quantity_on_hand']*r['unit_cost']for r in rows);row=dict(wh);row.update(total_quantity=qty,inventory_value=rnd(value),distinct_product_count=len({r['variant_id']for r in rows if r['variant_id']is not None}),avg_unit_cost=rnd(value/qty)if qty else None,max_single_item_value=rnd(maximum),inventory_concentration=rnd(maximum/value,'.0001')if value else None);final.append(row);exact[row['warehouse_id']]={'weighted_cost':value/qty if qty else None,'concentration':maximum/value if value else None}
 return {'stages':{'levels':cleanlevels,'warehouses':cleanwarehouses},'final':final,'exact':exact,'profile':{'raw_levels':len(levels),'raw_warehouses':len(warehouses),'nonpositive_lines':sum(r['quantity_on_hand']<=0 for r in cleanlevels),'numeric_null_counts':{n:sum(r[n]is None for r in levels)for n in('quantity_on_hand','quantity_available','quantity_reserved','unit_cost')},'whitespace_counts':{n:sum(isinstance(r[n],str)and r[n]!=r[n].strip()for r in warehouses)for n in warehouses[0]},'zero_denominators':[k for k,v in exact.items()if any(x is None for x in v.values())]}}
