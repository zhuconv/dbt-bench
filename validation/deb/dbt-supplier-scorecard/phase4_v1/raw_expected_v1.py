import collections,datetime,decimal,hashlib,json,pathlib
D=decimal.Decimal
METRICS=['supplier_id','supplier_code','supplier_name','supplier_type','total_pos','on_time_pos','on_time_delivery_rate','avg_days_late','total_quantity_received','total_quantity_accepted','total_quantity_rejected','overall_acceptance_rate','overall_defect_rate','total_po_value','total_invoice_value','overall_cost_variance','overall_cost_variance_pct']
SCORES=['supplier_id','supplier_code','supplier_name','supplier_type','on_time_delivery_rate','on_time_score','acceptance_rate','quality_score','cost_variance_pct','cost_score','composite_score','performance_tier']
STAGE={
 'suppliers':['supplier_id','supplier_code','supplier_name','supplier_type','lead_time_days','status'],
 'purchase_orders':['po_id','po_number','supplier_id','warehouse_id','status','total_amount','expected_date','ordered_at'],
 'purchase_order_receipts':['receipt_id','receipt_number','po_id','received_date','received_at','status'],
 'purchase_order_receipt_lines':['receipt_line_id','receipt_id','po_line_id','quantity_received','quantity_accepted','quantity_rejected','reject_reason'],
 'supplier_invoices':['invoice_id','invoice_number','supplier_id','po_id','invoice_date','total_amount','status']}
INTER={
 'int_po_delivery_performance':['po_id','supplier_id','expected_date','first_receipt_date','days_late','is_on_time'],
 'int_po_quality_performance':['po_id','supplier_id','total_quantity_received','total_quantity_accepted','total_quantity_rejected','acceptance_rate','defect_rate'],
 'int_po_cost_performance':['po_id','supplier_id','po_total','invoice_total','cost_variance','cost_variance_pct']}
def digest(x):return hashlib.sha256(json.dumps(x,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def save(p,x):p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,default=str,indent=2,sort_keys=True)+'\n')
def rounded(x,p=4):return D(x).quantize(D(1).scaleb(-p),rounding=decimal.ROUND_HALF_UP)
def div(a,b):return D(a)/D(b)if b else D(0)
def tier(x):return 'PLATINUM'if x>=90 else'GOLD'if x>=75 else'SILVER'if x>=60 else'BRONZE'if x>=40 else'AT_RISK'
def derive(data):
 raw={k:{'rows':len(v),'sha256':digest(v)}for k,v in data.items()}
 ss=data['suppliers'];po=data['purchase_orders'];rh=data['purchase_order_receipts'];rl=data['purchase_order_receipt_lines'];iv=data['supplier_invoices'];sup={r['supplier_id']:r for r in ss if r['status']=='ACTIVE'};eligible={r['po_id']:r for r in po if r['status']is not None and r['status']not in['DRAFT','CANCELLED']}
 stage={}
 for n,rows in data.items():
  kept=[]
  for original in rows:
   r=dict(original)
   if n=='suppliers'and r['status']!='ACTIVE':continue
   if n=='purchase_orders'and(r['status']is None or r['status']in['DRAFT','CANCELLED']):continue
   if n=='supplier_invoices'and(r['status']is None or r['status']=='DISPUTED'):continue
   if n=='purchase_order_receipts':r['received_date']=r['received_at'].date()if r['received_at']else None
   if n=='purchase_order_receipt_lines':
    for col in ['quantity_received','quantity_accepted','quantity_rejected']:r[col]=r[col]if r[col]is not None else D(0)
   kept.append([r[col]for col in STAGE[n]])
  stage['stg_sp__'+n]={'columns':STAGE[n],'rows':kept}
 receipts=collections.defaultdict(list);rmap={}
 for r in rh:receipts[r['po_id']].append(r);rmap[r['receipt_id']]=r
 quantities=collections.defaultdict(lambda:[D(0),D(0),D(0)])
 for r in rl:
  header=rmap.get(r['receipt_id'])
  if header:
   for i,col in enumerate(['quantity_received','quantity_accepted','quantity_rejected']):quantities[header['po_id']][i]+=r[col]if r[col]is not None else D(0)
 invoices=collections.defaultdict(lambda:D(0));invoice_counts=collections.Counter()
 for r in iv:
  if r['status']is not None and r['status']!='DISPUTED':invoices[r['po_id']]+=r['total_amount'];invoice_counts[r['po_id']]+=1
 delivery=[];quality=[];cost=[];unresolved=[]
 for pid,r in sorted(eligible.items()):
  if pid in receipts:
   dates=[x['received_at'].date()for x in receipts[pid]if x['received_at']is not None];first=min(dates)if dates else None;expected=r['expected_date'];ontime=bool(first is not None and expected is not None and first<=expected);late=0 if ontime else(first-expected).days if first is not None and expected is not None else None
   if first is None or expected is None:unresolved.append({'po_id':pid,'missing_delivery_date':True})
   delivery.append([pid,r['supplier_id'],expected,first,late,ontime])
  if quantities[pid][0]>0:
   rec,acc,rej=quantities[pid];quality.append([pid,r['supplier_id'],rec,acc,rej,rounded(div(acc,rec)),rounded(div(rej,rec))])
  if r['total_amount']is not None and r['total_amount']>0 and invoice_counts[pid]:
   total=r['total_amount'];amt=invoices[pid];cost.append([pid,r['supplier_id'],total,amt,amt-total,rounded(div(amt-total,total))])
 metrics=[];scores=[];exact=[];missing_delivery=[];missing_quality=[];missing_cost=[]
 for sid,s in sorted(sup.items()):
  ds=[r for r in delivery if r[1]==sid];qs=[r for r in quality if r[1]==sid];cs=[r for r in cost if r[1]==sid]
  if not ds:missing_delivery.append(sid);continue
  if not qs:missing_quality.append(sid)
  if not cs:missing_cost.append(sid)
  n=len(ds);ot=sum(r[5]for r in ds);late=[D(r[4])for r in ds if not r[5]and r[4]is not None];dr=rounded(div(ot,n));avg=rounded(sum(late,D(0))/len(late),2)if late else D(0);qty=[sum((r[i]for r in qs),D(0))for i in[2,3,4]];ar=rounded(div(qty[1],qty[0]));defect=rounded(div(qty[2],qty[0]));pt=sum((r[2]for r in cs),D(0));it=sum((r[3]for r in cs),D(0));cv=it-pt;vr=rounded(div(cv,pt));base=[sid,s['supplier_code'],s['supplier_name'],s['supplier_type']]
  metrics.append(base+[n,ot,dr,avg,*qty,ar,defect,pt,it,cv,vr]);ons=rounded(dr*100,2);qualitys=rounded(ar*100,2);costs=rounded(max(D(0),min(D(100),(D('.10')-vr)/D('.15')*100)),2);composite=rounded(ons*D('.40')+qualitys*D('.35')+costs*D('.25'),2)
  scores.append(base+[dr,ons,ar,qualitys,vr,costs,composite,tier(composite)]);exact.append({'supplier_id':sid,'on_time_rate':div(ot,n),'avg_days_late':div(sum(late,D(0)),len(late)),'acceptance_rate':div(qty[1],qty[0]),'defect_rate':div(qty[2],qty[0]),'cost_variance_pct':div(cv,pt),'cost_score_unrounded_from_reported_ratio':max(D(0),min(D(100),(D('.10')-vr)/D('.15')*100)),'composite_unrounded_from_reported_components':ons*D('.40')+qualitys*D('.35')+costs*D('.25')})
 out={**stage,'int_po_delivery_performance':{'columns':INTER['int_po_delivery_performance'],'rows':delivery},'int_po_quality_performance':{'columns':INTER['int_po_quality_performance'],'rows':quality},'int_po_cost_performance':{'columns':INTER['int_po_cost_performance'],'rows':cost},'fct_supplier_metrics':{'columns':METRICS,'rows':metrics},'fct_supplier_scorecard':{'columns':SCORES,'rows':scores}}
 return {'raw':raw,'relations':out,'exact_quotients':exact,'profile':{'active_suppliers':len(sup),'active_without_delivery':missing_delivery,'active_without_quality':missing_quality,'active_without_cost':missing_cost,'null_delivery_dates':unresolved,'eligible_pos':len(eligible),'receiptless_pos':sorted(set(eligible)-set(receipts)),'nonpositive_received_pos':sorted(pid for pid in eligible if quantities[pid][0]<=0),'nonpositive_po_amounts':sorted(pid for pid,r in eligible.items()if r['total_amount']is None or r['total_amount']<=0),'noneligible_status_counts':dict(collections.Counter(r['status']for r in po if r['po_id']not in eligible)),'disputed_invoices':sum(r['status']=='DISPUTED'for r in iv),'raw_null_counts':{n:dict(collections.Counter(k for r in rows for k,v in r.items()if v is None))for n,rows in data.items()}},'policy':'Instruction has no fixed decimal-place/tie requirement. This candidate retains reviewed reference representation: ratios 4 decimal places, avg days and scores 2. Exact quotients retained; no tolerance inferred from native checker. Original names remain a separate contract violation.'}
def compare(actual,expected,cols):
 a=sorted(actual,key=lambda r:str(r[0]));b=sorted(expected,key=lambda r:str(r[0]));diff=[]
 if len(a)!=len(b):return [{'row_count':[len(a),len(b)]}]
 for x,y in zip(a,b):
  for col,v,w in zip(cols,x,y):
   if v==w:continue
   if isinstance(v,(int,float,D))and isinstance(w,(int,float,D))and not isinstance(v,bool)and not isinstance(w,bool)and abs(D(str(v))-D(str(w)))<=D('1e-10'):continue
   diff.append({'key':x[0],'column':col,'actual':v,'expected':w})
 return diff
