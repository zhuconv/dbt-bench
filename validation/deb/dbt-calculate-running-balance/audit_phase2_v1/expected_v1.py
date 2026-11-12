"""Flat imperative source replay, exact money and rational non-money day averages."""
from collections import defaultdict,Counter
from decimal import Decimal as D,ROUND_HALF_UP,ROUND_HALF_EVEN,ROUND_HALF_DOWN
from fractions import Fraction as F
COLS={'customer_running_balance':['transaction_id','customer_id','transaction_type','transaction_date','transaction_amount','previous_balance','running_balance','transaction_sequence','balance_status','days_since_last_payment','is_high_balance','balance_change_direction','cumulative_orders','cumulative_payments','is_first_transaction','balance_trend','consecutive_orders','consecutive_payments','days_since_first_transaction'],'rpt_customer_account_summary':['customer_id','total_orders','total_payments','total_order_amount','total_payment_amount','final_balance','final_status','avg_days_between_payments','max_balance_reached','account_health_score','longest_order_streak','account_age_days'],'rpt_risk_assessment':['customer_id','final_balance','payment_frequency','balance_volatility','streak_risk','overall_risk_score','risk_category']}
KEYS={'customer_running_balance':['transaction_id'],'rpt_customer_account_summary':['customer_id'],'rpt_risk_assessment':['customer_id']}
def day(x):return x.date()if hasattr(x,'hour')else x

def derive(raw):
 by=defaultdict(list);nulls=[];money_ties=[];trim_differences=[]
 for name,rows in raw.items():
  for r in rows:
   if name=='orders'and(r['status']is None or r['status']in['CANCELLED','FAILED']):continue
   if name=='payments'and r['status']!='POSTED':continue
   tid,cid,stamp,amount=(r['order_id'],r['customer_id'],r['ordered_at'],r['grand_total'])if name=='orders'else(r['payment_id'],r['customer_id'],r['payment_date'],r['amount'])
   if None in[tid,cid,stamp,amount]:nulls.append(r);continue
   for x in[tid,cid]:
    if x!=x.strip():trim_differences.append(x)
   amount=D(str(amount));rounded=amount.quantize(D('.01'),rounding=ROUND_HALF_UP)
   if abs(amount)*100%1==D('.5'):money_ties.append({'transaction_id':tid,'amount':str(amount),'half_up':str(rounded),'half_even':str(amount.quantize(D('.01'),rounding=ROUND_HALF_EVEN)),'half_down':str(amount.quantize(D('.01'),rounding=ROUND_HALF_DOWN))})
   by[cid.strip()].append(dict(transaction_id=tid.strip(),customer_id=cid.strip(),transaction_type='ORDER'if name=='orders'else'PAYMENT',transaction_date=day(stamp),transaction_amount=rounded if name=='orders'else-rounded))
 if nulls:raise ValueError('Actual eligible rows have unspecified NULL identity/date/amount policy: '+str(len(nulls)))
 ledger=[];summary=[];risks=[];ties=0
 for cid,rs in sorted(by.items()):
  rs.sort(key=lambda r:(r['transaction_date'],r['transaction_id']));ties+=sum(v>1 for v in Counter(r['transaction_date']for r in rs).values());balance=D(0);last_payment=None;orders=payments=oc=pc=0;first=rs[0]['transaction_date'];rows=[];paydates=[]
  for i,r in enumerate(rs,1):
   before=balance;balance+=r['transaction_amount'];typ=r['transaction_type'];orders+=typ=='ORDER';payments+=typ=='PAYMENT';oc=oc+1 if typ=='ORDER'else 0;pc=pc+1 if typ=='PAYMENT'else 0
   rows.append(dict(**r,previous_balance=before,running_balance=balance,transaction_sequence=i,balance_status='CREDIT'if balance<0 else'ZERO'if balance==0 else'DEBIT',days_since_last_payment=(r['transaction_date']-last_payment).days if last_payment is not None else None,is_high_balance=int(balance>500),balance_change_direction='INCREASE'if balance>before else'DECREASE'if balance<before else'NO_CHANGE',cumulative_orders=orders,cumulative_payments=payments,is_first_transaction=int(i==1),balance_trend='IMPROVING'if balance<before else'WORSENING'if balance>before else'STABLE',consecutive_orders=oc,consecutive_payments=pc,days_since_first_transaction=(r['transaction_date']-first).days))
   if typ=='PAYMENT':last_payment=r['transaction_date'];paydates.append(last_payment)
  ledger+=rows;gaps=[(b-a).days for a,b in zip(paydates,paydates[1:])];average=F(sum(gaps),len(gaps))if gaps else None;maximum=max(r['running_balance']for r in rows);longest=max(r['consecutive_orders']for r in rows);sm=dict(customer_id=cid,total_orders=orders,total_payments=payments,total_order_amount=sum((r['transaction_amount']for r in rows if r['transaction_type']=='ORDER'),D(0)),total_payment_amount=sum((abs(r['transaction_amount'])for r in rows if r['transaction_type']=='PAYMENT'),D(0)),final_balance=balance,final_status=rows[-1]['balance_status'],avg_days_between_payments=average,max_balance_reached=maximum,account_health_score=5 if balance<=0 else 4 if balance<=100 else 3 if balance<=300 else 2 if balance<=500 else 1,longest_order_streak=longest,account_age_days=(rs[-1]['transaction_date']-first).days);summary.append(sm)
  frequency='LOW'if average is None or average>=45 else'HIGH'if average<15 else'MEDIUM';volatility='HIGH'if maximum>3*balance else'MEDIUM'if maximum>D('1.5')*balance else'LOW';streak='HIGH'if longest>=5 else'MEDIUM'if longest>=3 else'LOW';score={'HIGH':3,'MEDIUM':2,'LOW':1}[frequency]+{'HIGH':4,'MEDIUM':2,'LOW':1}[volatility]+{'HIGH':3,'MEDIUM':2,'LOW':1}[streak];risks.append(dict(customer_id=cid,final_balance=balance,payment_frequency=frequency,balance_volatility=volatility,streak_risk=streak,overall_risk_score=score,risk_category='CRITICAL'if score>=9 else'HIGH'if score>=7 else'MEDIUM'if score>=5 else'LOW'))
 ids=[r['transaction_id']for r in ledger]
 return dict(rows={'customer_running_balance':ledger,'rpt_customer_account_summary':summary,'rpt_risk_assessment':risks},columns=COLS,keys=KEYS,profile={'source_rows':{k:len(v)for k,v in raw.items()},'eligible_orders':sum(r['transaction_type']=='ORDER'for r in ledger),'posted_payments':sum(r['transaction_type']=='PAYMENT'for r in ledger),'customers':len(by),'duplicate_source_transaction_ids':len(ids)-len(set(ids)),'same_day_customer_groups':ties,'money_half_cent_cases':money_ties,'trim_differences':trim_differences,'average_day_ratios_not_exact_at_two_decimals':sum(r['avg_days_between_payments']is not None and(r['avg_days_between_payments']*100).denominator!=1 for r in summary)},scope='Input-only signed integer sequence replay; monetary amounts round two decimals. Average days is a nonmonetary exact rational and is not silently rounded to two decimals. No native/gold access.')
