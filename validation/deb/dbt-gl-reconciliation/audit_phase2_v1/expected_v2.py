"""Independent Python GL derivation; trial-side alternatives are not resolved by native."""
from collections import defaultdict
from decimal import Decimal as D
from datetime import datetime
COLS={'account_balances':('account_id','account_number','account_name','account_type','total_debits','total_credits','net_balance'),'trial_balance':('total_debit_balances','total_credit_balances','difference','is_balanced'),'out_of_balance_entries':('transaction_number','entry_date','total_debits','total_credits','imbalance_amount','line_count'),'unusual_balance_accounts':('account_id','account_number','account_name','account_type','account_subtype','net_balance','expected_direction','actual_direction','is_unusual'),'period_summary':('period_id','period_name','fiscal_year','fiscal_quarter','fiscal_month','start_date','end_date','period_status','total_debits','total_credits','net_activity','transaction_count','prior_period_net_activity','activity_change')}
KEYS={'account_balances':'account_id','trial_balance':None,'out_of_balance_entries':'transaction_number','unusual_balance_accounts':'account_id','period_summary':'period_id'}
def day(x):return x.date()if isinstance(x,datetime)else x
def money(x):return D(0)if x is None else D(x)
def derive(raw):
 accounts={r['account_id']:r for r in raw['accounts']};periods={r['period_id']:r for r in raw['periods']};assert len(accounts)==len(raw['accounts'])and len(periods)==len(raw['periods']);a=defaultdict(list);p=defaultdict(list);j=defaultdict(list)
 for r in raw['transactions']:a[r['account_id']].append(r);p[r['period_id']].append(r);j[r['transaction_number']].append(r)
 def totals(rs):return sum((money(r['debit_amount'])for r in rs),D(0)),sum((money(r['credit_amount'])for r in rs),D(0))
 out={k:[]for k in COLS};raw_sides=[];natural=[]
 for aid,rs in a.items():
  if aid not in accounts:continue
  x=accounts[aid];d,c=totals(rs);v=d-c;n=v if x['account_type']in ['ASSET','EXPENSE']else-v;raw_sides.append(v);natural.append(n);out['account_balances'].append({**{k:x[k]for k in ['account_id','account_number','account_name','account_type']},'total_debits':d,'total_credits':c,'net_balance':n});typ=x['account_type'];contra=x['account_subtype']=='CONTRA';expected='CREDIT'if typ in ['LIABILITY','EQUITY']or typ=='ASSET'and contra or typ=='REVENUE'and not contra else'DEBIT'if typ in ['ASSET','EXPENSE','REVENUE']else'UNKNOWN';actual='DEBIT'if v>D('.01')else'CREDIT'if v<-D('.01')else'ZERO'
  if actual!='ZERO'and actual!=expected:out['unusual_balance_accounts'].append({**{k:x[k]for k in ['account_id','account_number','account_name','account_type','account_subtype']},'net_balance':v,'expected_direction':expected,'actual_direction':actual,'is_unusual':True})
 def trial(vs):
  d=sum((max(v,D(0))for v in vs),D(0));c=sum((max(-v,D(0))for v in vs),D(0));return dict(total_debit_balances=d,total_credit_balances=c,difference=d-c,is_balanced=abs(d-c)<=D('.01'))
 alternatives={'raw_debit_minus_credit':trial(raw_sides),'natural_net_sign':trial(natural)};multi=[]
 for number,rs in j.items():
  d,c=totals(rs);dates={day(r['transaction_date'])for r in rs if r['transaction_date']is not None}
  if len(dates)>1:multi.append(number)
  if abs(d-c)>D('.01'):out['out_of_balance_entries'].append(dict(transaction_number=number,entry_date=min(dates)if dates else None,total_debits=d,total_credits=c,imbalance_amount=abs(d-c),line_count=len(rs)))
 chronological=sorted(periods.values(),key=lambda x:(x['fiscal_year'],x['fiscal_month']));prior=D(0)
 for x in chronological:
  rs=p[x['period_id']];d,c=totals(rs);n=d-c;out['period_summary'].append({**{k:x[k]for k in ['period_id','period_name','fiscal_year','fiscal_quarter','fiscal_month','start_date','end_date']},'period_status':x['status'],'total_debits':d,'total_credits':c,'net_activity':n,'transaction_count':sum(r['transaction_id']is not None for r in rs),'prior_period_net_activity':prior,'activity_change':n-prior});prior=n
 facts={'raw_transactions':len(raw['transactions']),'raw_accounts':len(accounts),'raw_periods':len(periods),'row_counts':{k:len(v)for k,v in out.items()},'journals_multiple_dates':multi,'accounts_without_transactions':sorted(set(accounts)-set(a)),'transactions_without_account':sum(r['account_id']not in accounts for r in raw['transactions']),'transactions_without_period':sum(r['period_id']not in periods for r in raw['transactions']),'period_order_keys_unique':len({(x['fiscal_year'],x['fiscal_month'])for x in chronological})==len(chronological),'trial_side_unresolved':alternatives['raw_debit_minus_credit']!=alternatives['natural_net_sign']}
 orphan_totals={aid:dict(total_debits=totals(rs)[0],total_credits=totals(rs)[1],transaction_count=len(rs))for aid,rs in a.items()if aid not in accounts}
 return {'outputs':out,'trial_alternatives':alternatives,'facts':facts,'required_account_ids':sorted(a),'orphan_account_raw_totals':orphan_totals,'all_account_raw_side_trial':trial([totals(rs)[0]-totals(rs)[1]for rs in a.values()])}
