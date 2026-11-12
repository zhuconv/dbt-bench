"""Actual compiled task semantics with typed, active division operands."""
from pathlib import Path
import sys,hashlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'audit_phase2_v1'))
from workflow_v3 import function_workflow,active_nodes,walk

def inspect(parsed,manifest,outputs):
 checks={};nodes={n['name']:n for n in manifest['nodes'].values()if n.get('resource_type')=='model'};asts={}
 for r in parsed:
  n=nodes[r['name']];assert hashlib.sha256(n['compiled_code'].encode()).hexdigest()==r['compiled_sha256'];asts[r['name']]=r['ast']
 checks['datediff']=function_workflow('fulfillment',asts['int_shipment_times']);divisions=[];bad=[]
 for name,ast in asts.items():
  active=active_nodes(ast)
  for node in active:
   if node.get('class')!='FUNCTION'or node.get('function_name')!='/':continue
   children=node['children'];flat=list(walk(children));kind=None
   if any(x.get('class')=='CAST'and x.get('cast_type',{}).get('id')=='DOUBLE'for x in flat):kind='explicit_DOUBLE_cast'
   elif children[0].get('function_name')=='ln':kind='LN_is_nonintegral_numeric'
   elif children[0].get('function_name')=='round' and any(x.get('class')=='COLUMN_REF'and x.get('column_names',[])[-1:]==['total_order_value_raw']for x in walk(children[0])):
    definitions=[x for x in active if x.get('alias')=='total_order_value_raw'];proper=any(x.get('function_name')=='sum' and x.get('children',[{}])[0].get('column_names',[])[-1:]==['grand_total']for x in definitions);typ=outputs['int_shipment_times']['table'].physical_sql_types['grand_total'];kind='SUM_decimal_order_value' if proper and typ.startswith(('DECIMAL','DOUBLE','FLOAT'))else None
   info={'model':name,'location':node.get('query_location'),'noninteger_basis':kind};divisions.append(info)
   if kind is None:bad.append(info)
 checks['division']={'status':'fail'if bad else'pass','reason':'Guideline purpose is avoiding integer division. Integer count divisions use explicit DOUBLE; four remaining divisions have proven LN or SUM(decimal grand_total) numerators, so no integer-only ratio is accepted. This declared operand-type interpretation supersedes old literal-per-node uncertainty only for the current reviewed form.','divisions':divisions,'unproven':bad}
 states=[c['status']for c in checks.values()];return {'status':'fail'if'fail'in states else'gap'if any(s in states for s in ('gap','pending'))else'pass','checks':checks,'scope':'Active parsed DATEDIFF source timestamp pairs and nonintegral division lineage; exact raw-input output values checked separately.'}
