"""Original HR-input replay; never imports the tested verifier or reference SQL.
Ambiguous graph/hourly/NULL-warehouse interpretations are explicitly retained.
"""
import argparse,collections,datetime as dt,decimal,hashlib,json,pathlib
from decimal import Decimal as D
S='retail_staging.';I='retail_intermediate.';M='retail_marts.'
def encode(x):
 if isinstance(x,D):return {'$type':'decimal','value':str(x)}
 if isinstance(x,(dt.datetime,dt.date)):return {'$type':type(x).__name__,'value':x.isoformat()}
 if isinstance(x,float):return {'$type':'float','value':repr(x)}
 if isinstance(x,dict):return {str(k):encode(v) for k,v in x.items()}
 if isinstance(x,(tuple,list)):return [encode(v) for v in x]
 return x
def decode(x):
 if isinstance(x,dict) and '$type' in x:
  t,v=x['$type'],x['value']
  if t in ('decimal','float'):return D(v)
  if t=='date':return dt.date.fromisoformat(v)
  if t=='datetime':return dt.datetime.fromisoformat(v)
  return v
 if isinstance(x,list):return [decode(v) for v in x]
 return x
def digest(x):return hashlib.sha256(json.dumps(encode(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()
def day(x):return x.date() if isinstance(x,dt.datetime) else x
def month(x):return day(x).replace(day=1)
def mean(xs):xs=[D(str(x)) for x in xs if x is not None];return sum(xs)/len(xs) if xs else None
def total(xs):xs=[D(str(x)) for x in xs if x is not None];return sum(xs,D(0)) if xs else None
def pick(r,cols):return {c:r.get(c) for c in cols.split()}
def group(rs,keys):
 out=collections.defaultdict(list)
 for r in rs:out[tuple(r[k] for k in keys)].append(r)
 return out
def derive_hr(c):
 queries={
 'employees':'SELECT employee_id,employee_number,first_name,last_name,email,hire_date,termination_date,manager_id,department_id,position_id,employment_type,status FROM HR.EMPLOYEES',
 'org':'SELECT hierarchy_id,employee_id,manager_id,level,path,effective_to FROM HR.ORG_HIERARCHY',
 'departments':'SELECT department_id,department_code,department_name,parent_department_id,manager_id,is_active FROM HR.DEPARTMENTS',
 'comp':'SELECT compensation_id,employee_id,compensation_type,amount,frequency,effective_from,effective_to FROM HR.EMPLOYEE_COMPENSATION',
 'time':'SELECT entry_id,employee_id,entry_date,hours_worked,entry_type,status FROM HR.TIME_ENTRIES',
 'positions':'SELECT position_id,position_title,job_level,department_id,is_active FROM HR.JOB_POSITIONS',
 'orders':'SELECT order_id,warehouse_id,ordered_at,shipped_at,grand_total,status FROM ORDERS.ORDERS',
 'shipments':'SELECT shipment_id,order_id,warehouse_id,shipped_at,delivered_at FROM ORDERS.SHIPMENTS'}
 raw={};meta=[]
 for name,q in queries.items():
  cur=c.execute(q);cols=[x[0].lower() for x in cur.description];raw[name]=[dict(zip(cols,r)) for r in cur.fetchall()];meta.append({'query':q,'rows':len(raw[name]),'sha256':digest(sorted(raw[name],key=lambda r:json.dumps(encode(r),sort_keys=True)))})
 anchor=max(r['termination_date'] or r['hire_date'] for r in raw['employees']);E=[]
 for r in raw['employees']:
  n=((r['termination_date'] or anchor)-r['hire_date']).days;E.append({**pick(r,'employee_id employee_number email hire_date termination_date manager_id department_id position_id employment_type status'),'full_name':None if r['first_name'] is None or r['last_name'] is None else r['first_name']+' '+r['last_name'],'is_active':int(r['status']=='ACTIVE'),'tenure_days':n,'tenure_years':D(n)/D('365.25')})
 O=[pick(r,'hierarchy_id employee_id manager_id level path') for r in raw['org'] if r['effective_to'] is None];Deps=[pick(r,'department_id department_code department_name parent_department_id manager_id') for r in raw['departments'] if r['is_active']];C=[pick(r,'compensation_id employee_id compensation_type amount frequency effective_from') for r in raw['comp'] if r['effective_to'] is None or r['effective_to']>=anchor];T=[pick(r,'entry_id employee_id entry_date hours_worked entry_type') for r in raw['time'] if r['status']=='APPROVED'];labels={1:'EXECUTIVE',2:'DIRECTOR',3:'MANAGER',4:'LEAD',5:'SENIOR',6:'JUNIOR',7:'ENTRY'};P=[{**pick(r,'position_id position_title department_id'),'position_level':labels.get(r['job_level'])} for r in raw['positions'] if r['is_active']];Orders=[pick(r,'order_id warehouse_id ordered_at shipped_at grand_total') for r in raw['orders'] if r['status'] is not None and r['status']!='CANCELLED' and r['ordered_at'] is not None];Ships=raw['shipments']
 eidx={r['employee_id']:r for r in E};didx={r['department_id']:r for r in Deps};pidx={r['position_id']:r for r in P};oidx={r['employee_id']:r for r in O};H=[]
 for r in E:
  man=eidx.get(r['manager_id'],{});H.append({**pick(r,'employee_id employee_number full_name manager_id department_id position_id status'),'manager_name':man.get('full_name'),'reports_to_manager':int(bool(man)),'has_terminated_manager':int(bool(man) and man['status']!='ACTIVE')})
 def graph(rows):
  direct=collections.defaultdict(set)
  for r in rows:
   if r['manager_id'] is not None:direct[r['manager_id']].add(r['employee_id'])
  closure={};cycles=[]
  for manager in direct:
   todo=list(direct[manager]);seen=set()
   while todo:
    child=todo.pop()
    if child in seen:continue
    seen.add(child);todo+=list(direct.get(child,set()))
   if manager in seen:cycles.append(manager)
   closure[manager]=seen-{manager}
  return direct,closure,cycles
 ed,ec,ecycles=graph(E);od,oc,ocycles=graph(O);Spans=[{'manager_id':m,'manager_name':eidx.get(m,{}).get('full_name'),'direct_reports':len(rs),'total_team_size':len(ec[m])} for m,rs in ed.items()];sidx={r['manager_id']:r for r in Spans}
 Productivity=[]
 for (emp,mon),rs in group([{**r,'month':month(r['entry_date'])} for r in T],['employee_id','month']).items():
  hours=total(r['hours_worked'] for r in rs);days=len({r['entry_date'] for r in rs if r['entry_date'] is not None});Productivity.append({'employee_id':emp,'month':mon,'total_hours_worked':hours,'days_worked':days,'avg_hours_per_day':hours/days if days and hours is not None else None})
 shipping=group(Ships,['order_id']);Warehouse=[]
 for (wh,mon),rs in group([{**r,'month':month(r['ordered_at'])} for r in Orders if r['warehouse_id'] is not None],['warehouse_id','month']).items():
  revenue=total(r['grand_total'] for r in rs);count=len({r['order_id'] for r in rs});shipids={s['shipment_id'] for r in rs for s in shipping[(r['order_id'],)] if s['shipment_id'] is not None};Warehouse.append({'warehouse_id':wh,'month':mon,'total_orders':count,'total_shipments':len(shipids),'total_revenue':revenue,'avg_revenue_per_order':revenue/count if count and revenue is not None else None})
 Comp=[];hourly_ambiguous=[]
 for (emp,),rs in group(C,['employee_id']).items():
  hr=[r['amount'] for r in rs if r['compensation_type']=='HOURLY' and r['amount'] is not None]
  if len(set(hr))>1:hourly_ambiguous.append({'employee_id':emp,'amount_candidates':hr,'reason':'Multiple current HOURLY records but no aggregation or effective_from precedence stated. MAX is one reference interpretation, not certified unique correct answer.'})
  annual=sum((r['amount'] for r in rs if r['compensation_type']=='SALARY' and r['frequency']=='ANNUAL' and r['amount'] is not None),D(0));Comp.append({'employee_id':emp,'total_annual_comp':annual,'hourly_rate':max(hr) if hr else None,'has_bonus':int(any(r['compensation_type']=='BONUS' for r in rs))})
 cidx={r['employee_id']:r for r in Comp};hidx={r['employee_id']:r for r in H};Master=[];Org=[]
 for e in E:
  h=hidx[e['employee_id']];dep=didx.get(e['department_id'],{});pos=pidx.get(e['position_id'],{});comp=cidx.get(e['employee_id'],{});span=sidx.get(e['employee_id'],{});lev=oidx.get(e['employee_id'],{}).get('level')
  Master.append({**pick(e,'employee_id employee_number full_name email hire_date termination_date tenure_years status employment_type department_id position_id'),**pick(h,'manager_id manager_name has_terminated_manager'),'department_name':dep.get('department_name'),**pick(pos,'position_title position_level'),'total_annual_comp':comp.get('total_annual_comp'),'last_updated_at':{'dynamic':'CURRENT_TIMESTAMP'}})
  if e['status']=='ACTIVE':Org.append({**pick(e,'employee_id full_name'),**pick(pos,'position_title'),**pick(h,'manager_id manager_name'),'department_name':dep.get('department_name'),'org_level':lev,'direct_reports':span.get('direct_reports',0),'total_team_size':span.get('total_team_size',0),'is_manager':int(span.get('direct_reports',0)>0),'is_overloaded':int(span.get('direct_reports',0)>12)})
 Capacity=[]
 for dep in Deps:
  active=[e for e in E if e['status']=='ACTIVE' and e['department_id']==dep['department_id']];counts=collections.Counter(e['employment_type'] for e in active);n=sum(counts[t] for t in ['FULL_TIME','PART_TIME','CONTRACTOR']);pay=total(cidx.get(e['employee_id'],{}).get('total_annual_comp') for e in active)
  Capacity.append({**pick(dep,'department_id department_name'),'manager_name':eidx.get(dep['manager_id'],{}).get('full_name'),'total_employees':n,'full_time_employees':counts['FULL_TIME'],'part_time_employees':counts['PART_TIME'],'contractors':counts['CONTRACTOR'],'avg_tenure_years':mean(e['tenure_years'] for e in active),'total_annual_payroll':pay,'avg_comp_per_employee':pay/n if pay is not None and n else None})
 Manager=[]
 for span in Spans:
  emp=eidx.get(span['manager_id'],{});dep=didx.get(emp.get('department_id'),{});pos=pidx.get(emp.get('position_id'),{});reports=[eidx[x] for x in ed[span['manager_id']]]
  Manager.append({**span,**pick(dep,'department_name'),**pick(pos,'position_title'),'span_of_control':span['direct_reports'],'team_avg_tenure_years':mean(e['tenure_years'] for e in reports),'team_total_comp':sum((cidx.get(e['employee_id'],{}).get('total_annual_comp') or D(0) for e in reports),D(0)),'is_overloaded':int(span['direct_reports']>12),'is_understaffed':int(span['direct_reports']<3 and pos.get('position_level') in ('MANAGER','DIRECTOR'))})
 Trends=[];whmonth=group(Warehouse,['month'])
 for (mon,),rs in group(Productivity,['month']).items():
  employees=len({r['employee_id'] for r in rs});hours=total(r['total_hours_worked'] for r in rs);wr=whmonth[(mon,)];orders=sum(r['total_orders'] for r in wr) if wr else None;revenue=total(r['total_revenue'] for r in wr);Trends.append({'month':mon,'total_employees_active':employees,'total_hours_worked':hours,'avg_hours_per_employee':hours/employees if employees and hours is not None else None,'total_orders_processed':orders,'orders_per_employee':D(orders)/employees if employees and orders is not None else None,'revenue_per_employee':revenue/employees if employees and revenue is not None else None})
 out={S+'stg_hr__employees':E,S+'stg_hr__org_hierarchy':O,S+'stg_hr__departments':Deps,S+'stg_hr__compensation':C,S+'stg_hr__time_entries':T,S+'stg_hr__positions':P,S+'stg_orders':Orders,S+'stg_shipments':Ships,I+'int_hr__employee_hierarchy':H,I+'int_hr__manager_spans':Spans,I+'int_hr__employee_productivity':Productivity,I+'int_hr__warehouse_productivity':Warehouse,I+'int_hr__compensation_summary':Comp,M+'mart_employee_master':Master,M+'mart_org_structure':Org,M+'mart_department_capacity':Capacity,M+'mart_manager_effectiveness':Manager,M+'mart_workforce_trends':Trends}
 keys={r:('employee_id','month') if r.endswith('employee_productivity') else ('warehouse_id','month') if r.endswith('warehouse_productivity') else ('month',) if r.endswith('workforce_trends') else ('manager_id',) if r.endswith(('manager_spans','manager_effectiveness')) else ('hierarchy_id',) if r.endswith('org_hierarchy') else ('department_id',) if r.endswith(('departments','department_capacity')) else ('compensation_id',) if r.endswith('stg_hr__compensation') else ('entry_id',) if r.endswith('time_entries') else ('position_id',) if r.endswith('positions') else ('order_id',) if r.endswith('stg_orders') else ('shipment_id',) if r.endswith('stg_shipments') else ('employee_id',) for r in out}
 graph_conflicts=[{'employee_id':e['employee_id'],'employee_manager':e['manager_id'],'current_org_manager':oidx[e['employee_id']]['manager_id']} for e in E if e['employee_id'] in oidx and e['manager_id']!=oidx[e['employee_id']]['manager_id']]
 ambiguities={'manager_graph':{'conflicting_employee_rows':len(graph_conflicts),'conflicts':graph_conflicts,'employee_graph_cycles':ecycles,'org_graph_cycles':ocycles,'employee_graph_team_sizes':{m:len(v) for m,v in ec.items()},'current_org_graph_team_sizes':{m:len(v) for m,v in oc.items()},'reason':'185 current organizational manager links disagree with employee manager links; source precedence for all indirect team size is not explicit. Direct employee-manager relation and terminated-manager flags can be separately tested.'},'hourly_rate':hourly_ambiguous,'null_warehouse':{'eligible_orders':sum(r['warehouse_id'] is None for r in Orders),'reason':'NULL warehouse grouping/exclusion is not explicit. Expected report branch below excludes NULL warehouses as original; monthly hours and unique employee counts do not depend on that choice.'},'employment_values':dict(collections.Counter(e['employment_type'] for e in E)),'department_tenure_payroll_scope':'Explicit INTERN exclusion concerns three employment counts; avg tenure/payroll computed over active department employees. No CONTRACT→CONTRACTOR normalization invented.'}
 return out,{'sources':meta,'reference_date':anchor,'keys':keys,'ambiguities':ambiguities,'expected_rows':{r:len(v) for r,v in out.items()},'columns':{r:list(v[0]) if v else [] for r,v in out.items()},'tested_checker_invoked_to_define_validity':False}
def compare_hr(expected,actual,metadata,interval=None):
 diffs=[];dynamic=[]
 for rel,rows in expected.items():
  keys=metadata['keys'][rel];key=lambda r:tuple(r.get(k) for k in keys);by={key(r):r for r in rows};rs=actual.get(rel,[]);counts=collections.Counter(key(r) for r in rs)
  for k in set(by)-set(counts):diffs.append({'relation':rel,'key':k,'kind':'missing_key'})
  for k,n in counts.items():
   if k not in by:diffs.append({'relation':rel,'key':k,'kind':'extra_key'})
   if n!=1:diffs.append({'relation':rel,'key':k,'kind':'duplicate_key','count':n})
  for r in rs:
   if key(r) not in by:continue
   for col,exp in by[key(r)].items():
    val=r.get(col)
    if col=='last_updated_at':
     stamp=val if isinstance(val,dt.datetime) else dt.datetime.fromisoformat(str(val));stamp=stamp.replace(tzinfo=dt.timezone.utc) if stamp.tzinfo is None else stamp;ok=interval[0]<=stamp<=interval[1] if interval else None;dynamic.append({'key':key(r),'actual':stamp,'interval_check':ok});continue
    if exp is None or val is None:same=exp is None and val is None
    elif isinstance(exp,(D,int,float)) and isinstance(val,(D,int,float)):same=abs(D(str(exp))-D(str(val)))<=D('0.00000001')
    elif isinstance(exp,dt.date) and not isinstance(exp,dt.datetime):same=exp==day(val)
    else:same=exp==val
    if not same:diffs.append({'relation':rel,'key':key(r),'column':col,'expected':exp,'actual':val})
 return diffs,dynamic
if __name__=='__main__':
 import duckdb
 p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);p.add_argument('--result',required=True);p.add_argument('--output',required=True);a=p.parse_args();snap=pathlib.Path(a.snapshot);output=pathlib.Path(a.output);result=json.loads(pathlib.Path(a.result).read_text());interval=tuple(dt.datetime.fromisoformat(result['agent_execution'][k].replace('Z','+00:00')) for k in ['started_at','finished_at'])
 with duckdb.connect('/app/database/retail.duckdb',read_only=True) as c:expected,meta=derive_hr(c)
 actual={};snapshot=json.loads(snap.read_text())
 for o in snapshot['outputs']:
  cols=o['specification']['columns'] or [r['name'] for r in o['schema']];actual[o['specification']['relation']]=[dict(zip(cols,decode(json.loads(line)))) for line in (snap.parent/o['file']).read_text().splitlines()]
 differences,dynamic=compare_hr(expected,actual,meta,interval);summary=dict(collections.Counter(d['relation']+'.'+d.get('column',d.get('kind')) for d in differences));out={'task_key':'deb__dbt-hr-analytics','derivation':meta,'differences':differences,'counts':summary,'dynamic_last_updated_at':{'actual_execution_interval':interval,'all_values_in_interval':all(d['interval_check'] for d in dynamic),'values':dynamic},'scope':'All declared values inspected. Expected branch explicitly uses employee manager graph, MAX candidate hourly rate and excludes NULL warehouse as reference does; ambiguities remain unresolved and this is not whole-task baseline certification. Clear SALARY/ANNUAL and monthly-hours aggregation defects are independent of those ambiguities. No checker invoked.','model_requests':0};output.write_text(json.dumps(encode(out),indent=2)+'\n');output.with_name(output.stem+'-expected.json').write_text(json.dumps(encode(expected),indent=2)+'\n');print(json.dumps({'rows':meta['expected_rows'],'columns':sum(len(v) for v in meta['columns'].values()),'diffs':summary,'dynamic_in_interval':out['dynamic_last_updated_at']['all_values_in_interval'],'employee_graph_cycles':len(meta['ambiguities']['manager_graph']['employee_graph_cycles'])}))
