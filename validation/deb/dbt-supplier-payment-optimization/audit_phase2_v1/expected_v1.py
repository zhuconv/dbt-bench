import collections,datetime,decimal,hashlib,json,pathlib,re
D=decimal.Decimal;ANALYSIS=datetime.date(2024,12,31)
COLS=['invoice_id','supplier_id','supplier_name','supplier_rating','invoice_date','due_date','invoice_amount','currency_code','amount_usd','days_until_due','aging_bucket','days_past_optimal','payment_terms_code','early_payment_discount_pct','discount_deadline','potential_savings_usd','discount_opportunity_status','annualized_discount_roi','discount_roi_tier','payment_priority_score','priority_rank','priority_tier','supplier_risk_score','supplier_risk_category','supplier_invoice_concentration','recommended_payment_date','payment_strategy','cash_outflow_week','cumulative_outflow_usd','weekly_outflow_usd','working_capital_days','float_benefit_usd']
def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def digest(x):return hashlib.sha256(json.dumps(x,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def save(p,x):p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,default=str,indent=2,sort_keys=True)+'\n')
def rnd(x):return D(x).quantize(D('.01'),rounding=decimal.ROUND_HALF_UP)
def derive(data):
 data={k.upper():v for k,v in data.items()}
 raw={k:{'rows':len(v),'sha256':digest(v)}for k,v in data.items()}
 suppliers={r['supplier_id']:r for r in data['SUPPLIERS']};invoices=[];excluded=[];fx_ties=[];term_cases=[]
 for i in data['SUPPLIER_INVOICES']:
  if i['status']not in['OPEN','PENDING']:continue
  if i['supplier_id']not in suppliers:excluded.append({'invoice_id':i['invoice_id'],'reason':'missing_supplier'});continue
  s=suppliers[i['supplier_id']];dt=i['invoice_date'];due=i['due_date'];cur=i['currency_code']
  if cur=='USD':fx=D(1)
  else:
   rates=[r for r in data['CURRENCY_EXCHANGE_RATES']if r['from_currency']==cur and r['to_currency']=='USD'and r['effective_date']<=dt]
   if not rates:excluded.append({'invoice_id':i['invoice_id'],'reason':'no_asof_fx'});continue
   latest=max(r['effective_date']for r in rates);choices={r['exchange_rate']for r in rates if r['effective_date']==latest}
   if len(choices)>1:fx_ties.append({'invoice_id':i['invoice_id'],'rates':sorted(choices)})
   fx=sorted(choices)[0]
  terms=s['payment_terms'];m=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*/\s*(\d+)\s*NET\s*(\d+)\s*',terms or '',re.I);pct=D(m[1])if m else D(0);discount_days=int(m[2])if m else None;net_days=int(m[3])if m else None;deadline=dt+datetime.timedelta(days=discount_days)if m else None;available=bool(pct>0 and deadline and deadline>=ANALYSIS);status='Available'if available else'Expired'if pct>0 and deadline else'Not Applicable';amount=rnd(i['total_amount']*fx);days=(due-ANALYSIS).days if due else None;age=(ANALYSIS-dt).days
  bucket='Not Yet Due'if days is not None and days>0 else'1-30 Days Overdue'if days is not None and days>=-30 else'31-60 Days Overdue'if days is not None and days>=-60 else'61-90 Days Overdue'if days is not None and days>=-90 else'Over 90 Days Overdue';optimal=deadline if available else due;past=max(0,(ANALYSIS-optimal).days)if optimal else None;savings=rnd(amount*pct/100)if available else D(0);roi=rnd(pct/(100-pct)*D(365)/(net_days-discount_days)*100)if available and net_days>discount_days and pct<100 else D(0);roi_tier='Exceptional'if roi>=36 else'Good'if roi>=18 else'Marginal'if roi>0 else'Not Applicable'
  term_cases.append({'invoice_id':i['invoice_id'],'terms':terms,'discount_pct':pct,'discount_days':discount_days,'net_days':net_days,'available':available,'roi_exact':pct/(100-pct)*D(365)/(net_days-discount_days)*100 if available and net_days>discount_days and pct<100 else D(0)})
  invoices.append({'invoice_id':i['invoice_id'],'supplier_id':i['supplier_id'],'supplier_name':s['supplier_name'],'supplier_rating':s['rating']if s['rating']is not None else D(3),'invoice_date':dt,'due_date':due,'invoice_amount':rnd(i['total_amount']),'currency_code':cur,'amount_usd':amount,'days_until_due':days,'aging_bucket':bucket,'days_past_optimal':past,'payment_terms_code':terms,'early_payment_discount_pct':pct,'discount_deadline':deadline,'potential_savings_usd':savings,'discount_opportunity_status':status,'annualized_discount_roi':roi,'discount_roi_tier':roi_tier,'days_since_invoice':age})
 total=sum((r['amount_usd']for r in invoices),D(0));supplier_totals=collections.defaultdict(lambda:D(0))
 for r in invoices:supplier_totals[r['supplier_id']]+=r['amount_usd']
 maxamt=max(r['amount_usd']for r in invoices);policies={};details={}
 for policy in ['raw_concentration','rounded_concentration']:
  rows=[dict(r)for r in invoices];rounding=[]
  for r in rows:
   days=r['days_until_due'];ratio=supplier_totals[r['supplier_id']]/total;conc=rnd(ratio*100);used=ratio if policy=='raw_concentration'else conc/100;overdue=D(1)if days<-90 else D('.75')if days<-60 else D('.5')if days<-30 else D('.25')if days<=0 else D(0);age=max(D(0),min(D(1),D(r['days_since_invoice'])/180));rating=max(D(0),min(D(1),(5-r['supplier_rating'])/4));riskexact=overdue*35+min(D('.5'),used)*2*25+rating*20+age*20;risk=rnd(riskexact);riskcat='Critical Risk'if risk>=75 else'High Risk'if risk>=50 else'Medium Risk'if risk>=25 else'Low Risk';r.update(supplier_invoice_concentration=conc,supplier_risk_score=risk,supplier_risk_category=riskcat)
   prioover=D(1)if days<-90 else D('.8')if days<-60 else D('.6')if days<-30 else D('.4')if days<=0 else D(0);discount={'Exceptional':D(1),'Good':D('.75'),'Marginal':D('.5'),'Not Applicable':D(0)}[r['discount_roi_tier']];priority=prioover*30+risk/100*25+discount*20+r['amount_usd']/maxamt*15+r['supplier_rating']/5*10;r['payment_priority_score']=rnd(priority)
   strategy='Take Discount'if r['discount_opportunity_status']=='Available'and r['discount_roi_tier']in['Exceptional','Good']else'Immediate Payment'if'Overdue'in r['aging_bucket']and riskcat in['High Risk','Critical Risk']else'Defer Payment'if r['aging_bucket']=='Not Yet Due'and riskcat=='Low Risk'and days>14 else'Pay On Due Date';recommended=r['discount_deadline']if strategy=='Take Discount'else ANALYSIS if strategy=='Immediate Payment'else r['due_date'];wc=(recommended-r['invoice_date']).days;r.update(payment_strategy=strategy,recommended_payment_date=recommended,cash_outflow_week=recommended.isocalendar()[1],working_capital_days=wc,float_benefit_usd=rnd(r['amount_usd']*D('.05')/365*wc));rounding.append({'invoice_id':r['invoice_id'],'risk_exact':riskexact,'priority_exact':priority,'concentration_exact':ratio*100})
  ranked=sorted(rows,key=lambda r:(-r['payment_priority_score'],r['due_date'],r['invoice_id']));size=len(rows)//4;extra=len(rows)%4;tiers=[]
  for n,t in enumerate(['Critical','High','Medium','Low']):tiers.extend([t]*(size+(n<extra)))
  for rank,(r,t)in enumerate(zip(ranked,tiers),1):r['priority_rank']=rank;r['priority_tier']=t
  weekly=collections.defaultdict(lambda:D(0))
  for r in rows:weekly[r['cash_outflow_week']]+=r['amount_usd']
  running=D(0)
  for r in sorted(rows,key=lambda r:(r['recommended_payment_date'],r['invoice_id'])):running+=r['amount_usd'];r['cumulative_outflow_usd']=rnd(running);r['weekly_outflow_usd']=rnd(weekly[r['cash_outflow_week']])
  policies[policy]=[[r[col]for col in COLS]for r in sorted(rows,key=lambda r:r['invoice_id'])];details[policy]=rounding
 return {'raw':raw,'columns':COLS,'policies':policies,'exact_values':details,'term_cases':term_cases,'excluded':excluded,'fx_ties':fx_ties,'profile':{'rows':len(invoices),'currency_counts':dict(collections.Counter(r['currency_code']for r in invoices)),'aging_counts':dict(collections.Counter(r['aging_bucket']for r in invoices)),'terms_counts':dict(collections.Counter(str(r['payment_terms_code'])for r in invoices)),'future_invoice_count':sum(r['days_since_invoice']<0 for r in invoices),'amount_usd_total':total},'interpretations':{'future_invoice_age':'Current all12 invoice dates follow fixed analysis date. Clamp age component to0 to preserve specified0-100score; negative age weighting policy is not imposed.','concentration':'Both exact supplier ratio and displayed2decimal percentage input to risk retained, because instruction supplies a reported rounded percentage and a concentration ratio formula.'}}
