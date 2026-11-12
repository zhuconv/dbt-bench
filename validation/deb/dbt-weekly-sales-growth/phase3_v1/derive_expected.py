from pathlib import Path
from datetime import date,datetime
from decimal import Decimal
import importlib.util

def derive(inputs,decode):
 p=Path(__file__).with_name('raw_derivation_v1.py');spec=importlib.util.spec_from_file_location('weekly_raw_derive',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 source=inputs['relations']['main.orders'];cols=[v['name'].lower()for v in source['schema']];raw=[dict(zip(cols,map(decode,row)))for row in source['rows']];result=m.derive(raw)
 for model,rows in result.items():
  for row in rows:
   for name,value in row.items():
    if value is None:continue
    if name=='week_start':row[name]=date.fromisoformat(value)
    elif name=='ordered_at':row[name]=datetime.fromisoformat(value)
    elif name in m.MONEY or name in ('grand_total','revenue_growth_pct'):row[name]=Decimal(value)
 return result
