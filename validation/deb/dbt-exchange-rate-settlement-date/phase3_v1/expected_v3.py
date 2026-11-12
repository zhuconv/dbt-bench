"""Independent Python grouped arithmetic under the new four-key blind interpretation.
Missing-day ASOF/earliest and unknown-currency fallback are explicit policy.
"""
from collections import defaultdict,Counter
from decimal import Decimal as D,ROUND_HALF_UP
from fractions import Fraction as F
COLS={'fact_revenue':['order_date','year','month','quarter','day_of_week','week_of_year','currency_code','source_system','total_orders','unique_customers','total_line_items','gross_revenue','total_discounts','net_revenue','total_tax','total_revenue','avg_unit_price','avg_line_value','avg_order_value','total_units_ordered','total_units_shipped','fulfillment_rate','cancellation_rate','discount_rate','dbt_updated_at','settlement_date','fx_rate','net_revenue_usd','total_revenue_usd']}
KEYS={'fact_revenue':['order_date','settlement_date','currency_code','source_system']}
def day(x):
 if isinstance(x,str):
  from datetime import date
  return date.fromisoformat(x[:10])
 return x.date()if hasattr(x,'hour')else x

def rational(x):return x if isinstance(x,F)else F(D(str(x)))
def total(values):
 v=[rational(x)for x in values if x is not None];return sum(v,F(0))if v else None

def mean(values):
 v=[rational(x)for x in values if x is not None];return sum(v,F(0))/len(v)if v else None

def cents(v):
 if v is None:return None
 x=D(v.numerator)/D(v.denominator);return x.quantize(D('.01'),rounding=ROUND_HALF_UP)

def derive(raw):
 payments=defaultdict(list);rates=defaultdict(list)
 for r in raw['payments']:payments[r['order_id']].append(r['processed_at'])
 for r in raw['rates']:
  if r['to_currency']=='USD':rates[(r['from_currency'],day(r['rate_date']))].append(r['rate'])
 groups=defaultdict(list);original=defaultdict(list);missing=[];null_payments=[];multiple_rates=[];timestamp_conflicts=[]
 for r in raw['sales']:
  if r['order_date']is None:continue
  original[(r['order_date'],r['currency_code'],r['source_system'])].append(r);p=payments.get(r['order_id'].strip()if isinstance(r['order_id'],str)else r['order_id']);times=[x for x in(p or[])if x is not None];settlement=day(max(times))if times else day(r['order_date'])
  if p and not times:null_payments.append(r['order_id'])
  if r['currency_code']=='USD':fx=F(1);known=True
  else:
   matches=[v for v in rates.get((r['currency_code'],settlement),[])if v is not None]
   if len(matches)>1:multiple_rates.append((r['currency_code'],settlement))
   history=[(d,v)for(c,d),vs in rates.items()if c==r['currency_code']and c is not None for v in vs if v is not None];past=[(d,v)for d,v in history if d<=settlement];fallback=max(past,key=lambda x:x[0])[1]if past else min(history,key=lambda x:x[0])[1]if history else 1;fx=rational(matches[0]if matches else fallback);known=bool(matches)
   if not matches:missing.append((r['order_id'],r['currency_code'],settlement))
  groups[(r['order_date'],settlement,r['currency_code'],r['source_system'])].append((r,fx,known))
 rows=[]
 for key,items in groups.items():
  order_date,settlement,currency,source=key;rs=[x[0]for x in items];d=day(order_date);orders=len({r['order_id']for r in rs if r['order_id']is not None});lines=sum(r['order_line_id']is not None for r in rs);gross=total(r['extended_price']for r in rs);discount=total(r['discount_amount']for r in rs);net=total(rational(r['extended_price'])-rational(r['discount_amount'])if r['extended_price']is not None and r['discount_amount']is not None else None for r in rs);rev=total(r['line_total']for r in rs)
  net_usd=total((rational(r['extended_price'])-rational(r['discount_amount']))*fx for r,fx,known in items if r['extended_price']is not None and r['discount_amount']is not None);rev_usd=total(rational(r['line_total'])*fx for r,fx,known in items if r['line_total']is not None);rows.append(dict(order_date=order_date,settlement_date=settlement,currency_code=currency,source_system=source,year=d.year,month=d.month,quarter=(d.month-1)//3+1,day_of_week=(d.weekday()+1)%7,week_of_year=d.isocalendar().week,total_orders=orders,unique_customers=len({r['customer_id']for r in rs if r['customer_id']is not None}),total_line_items=lines,gross_revenue=gross,total_discounts=discount,net_revenue=net,total_tax=total(r['tax_amount']for r in rs),total_revenue=rev,avg_unit_price=mean(r['unit_price']for r in rs),avg_line_value=mean(r['extended_price']for r in rs),avg_order_value=(rev/orders if rev is not None else None)if orders else F(0),total_units_ordered=total(r['quantity_ordered']for r in rs),total_units_shipped=total(r['quantity_shipped']for r in rs),fulfillment_rate=F(sum(r['is_fully_shipped']is True for r in rs),lines)if lines else F(0),cancellation_rate=F(sum(r['is_cancelled']is True for r in rs),lines)if lines else F(0),discount_rate=(discount/gross if discount is not None else None)if gross is not None and gross>0 else F(0),fx_rate=sum(x[1]for x in items)/len(items),net_revenue_usd=cents(net_usd),total_revenue_usd=cents(rev_usd),_all_fx_known=all(x[2]for x in items)))
 contrasts=[]
 for key,rs in original.items():
  dates={day(max([x for x in payments.get(r['order_id'],[])if x is not None]))if any(x is not None for x in payments.get(r['order_id'],[]))else day(r['order_date'])for r in rs}
  if len(dates)>1:contrasts.append({'original_key':[str(x)for x in key],'settlement_dates':sorted(str(x)for x in dates),'source_rows':len(rs)})
 return dict(rows={'fact_revenue':rows},columns=COLS,keys=KEYS,profile={'source_rows':{k:len(v)for k,v in raw.items()},'original_three_key_groups':len(original),'settlement_four_key_groups':len(groups),'grain_conflicts':contrasts,'missing_rate_sales':len(missing),'missing_rate_groups':len({(str(c),str(d))for _,c,d in missing}),'existing_payment_all_null_dates':len(set(null_payments)),'multiple_rate_matches':len(set(multiple_rates)),'null_key_groups':sum(r['currency_code']is None or r['source_system']is None for r in rows)},scope='New explicit four-key variant. Reviewed blind policy: all payment statuses, latest processed_at, trimmed order ID, ASOF/earliest fallback then unknown-currency rate1; NULL local amounts remain NULL. Original policy impact counts retained; preserved dynamic timestamp uses actual execution interval.')
