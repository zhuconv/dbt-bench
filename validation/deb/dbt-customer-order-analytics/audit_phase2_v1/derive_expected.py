import pathlib,json,hashlib,collections,datetime,decimal
D=decimal.Decimal
COLS=['customer_id','customer_name','email','order_count','total_spend','avg_order_value','first_order_date','last_order_date','days_since_last_order','customer_tier']
def digest(x):return hashlib.sha256(json.dumps(x,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def clean(x):return str(x).strip()if x is not None else None
def rnd(x):return x.quantize(D('.01'),rounding=decimal.ROUND_HALF_UP)
def derive(c,trim=True,include_invalid=False):
 customers=c.execute('SELECT customer_id,first_name,last_name,email FROM main.customers').fetchall();orders=c.execute('SELECT order_id,customer_id,status,grand_total,ordered_at FROM ORDERS.ORDERS').fetchall();by=collections.defaultdict(list);eligible=[];transform=clean if trim else lambda x:x
 for r in orders:
  stat=clean(r[2]).upper()if r[2]is not None else None
  if stat is not None and(include_invalid or stat not in['CANCELLED','RETURNED','FAILED']):by[transform(r[1])].append(r);eligible.append(r)
 expected=[];precise={};boundaries=[]
 for raw in customers:
  cid,first,last,email=[transform(v)for v in raw];rows=by.get(cid,[])if cid is not None else[]
  if not rows:continue
  n=len(rows);values=[D(str(r[3]))for r in rows if r[3]is not None];money=sum(values,D(0));avg=money/len(values)if values else None;dates=[r[4].date()for r in rows if r[4]is not None];mn=min(dates)if dates else None;mx=max(dates)if dates else None;tier='VIP'if n>=5 else'Regular'if n>=3 else'New';expected.append([cid,(first+' '+last)if first is not None and last is not None else None,email,n,rnd(money),rnd(avg)if avg is not None else None,mn,mx,(datetime.date(2024,12,1)-mx).days if mx is not None else None,tier]);precise[cid]={'4':money,'5':avg}
  if avg is not None and abs(avg-rnd(avg))==D('.005'):boundaries.append({'customer_id':cid,'exact_avg':avg})
 profile={'customer_rows':len(customers),'order_rows':len(orders),'eligible_order_rows':len(eligible),'eligible_customer_rows':len(expected),'duplicate_output_ids':len(expected)-len({r[0]for r in expected}),'output_null_cells':sum(v is None for r in expected for v in r),'tier_counts':dict(collections.Counter(r[9]for r in expected)),'eligible_null_order_date_rows':sum(r[4]is None for r in eligible),'eligible_duplicate_order_ids':len(eligible)-len({clean(r[0])for r in eligible}),'half_cent_aov_ties':boundaries,'exact_money_values':precise}
 return expected,[{'relation':'main.customers','columns':['customer_id','first_name','last_name','email'],'rows':len(customers),'sha256':digest(customers)},{'relation':'ORDERS.ORDERS','columns':['order_id','customer_id','status','grand_total','ordered_at'],'rows':len(orders),'sha256':digest(orders)}],profile
if __name__=='__main__':
 with duckdb.connect('/app/database/retail.duckdb',read_only=True)as c:e,r,p=derive(c)
 pathlib.Path('/review/customer-order-original-derived.json').write_text(json.dumps({'columns':COLS,'expected':e,'raw':r,'profile':p,'scope':'IndependentPython trim/statusfilter/group/join SUM/AVG/date/tier derivation. SourceORDERS.CUSTOMERS is actually absent; main.customers proven byte-value identical to CUSTOMER.CUSTOMERS; this sourcepath discrepancy remains explicit.'},default=str,indent=2)+'\n');print(r,{k:v for k,v in p.items()if k!='exact_money_values'})
