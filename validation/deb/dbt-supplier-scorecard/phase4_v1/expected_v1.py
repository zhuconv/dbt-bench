"""Exact rational audit inputs, extending retained raw grouping without reference SQL."""
from fractions import Fraction as F
from raw_expected_v1 import derive as derive_display, METRICS, SCORES, STAGE, INTER

def derive(raw):
 out=derive_display(raw);rels=out['relations'];delivery=rels['int_po_delivery_performance']['rows'];quality=rels['int_po_quality_performance']['rows'];cost=rels['int_po_cost_performance']['rows'];exact={}
 for row in rels['fct_supplier_metrics']['rows']:
  sid=row[0];ds=[r for r in delivery if r[1]==sid];qs=[r for r in quality if r[1]==sid];cs=[r for r in cost if r[1]==sid];late=[F(r[4])for r in ds if not r[5]and r[4]is not None];q=[sum((F(r[i])for r in qs),F(0))for i in [2,3,4]];po=sum((F(r[2])for r in cs),F(0));inv=sum((F(r[3])for r in cs),F(0));rat=lambda n,d:F(n)/d if d else None
  values={'on_time_delivery_rate':rat(sum(r[5]for r in ds),len(ds)),'avg_days_late':rat(sum(late,F(0)),len(late)),'overall_acceptance_rate':rat(q[1],q[0]),'overall_defect_rate':rat(q[2],q[0]),'overall_cost_variance_pct':rat(inv-po,po),'total_po_value':po,'total_invoice_value':inv,'overall_cost_variance':inv-po};exact[sid]={k:str(v)if v is not None else None for k,v in values.items()}
 
 for sid in exact:
  if exact[sid]['overall_cost_variance_pct'] is None:exact[sid]['overall_cost_variance_pct']='0'
 out['exact_fractions']=exact;out['policy']='Original raw grouping; exact rational quotients retain full numerator/denominator. Display precision is not required by task: accept exact values and correctly nearest-rounded retained4-place rates/2-place scores or finer. Coarser representations which are actually nearest-rounded remain unresolved rather than automatically wrong. Zero-denominator conventional0/NULL separately permitted under coalesce/nullif guidance, not asserted unique.'
 return out
