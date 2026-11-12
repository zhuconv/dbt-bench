"""Reviewed independent raw-order grouping and chronological array replay.
First-month Emerging/NULL clauses and exact rounding policies are explicit parameters.
No candidate/gold/native checker access.
"""
import sys
sys.dont_write_bytecode=True
import json,hashlib,pathlib,datetime,decimal,collections
D=decimal.Decimal;decimal.getcontext().prec=40
RAW=['order_id','customer_id','ordered_at','grand_total','status','order_source']
STAGING=['order_id','customer_id','ordered_at','grand_total','order_source']
METRICS=['month_start','channel','order_count','unique_customers','total_revenue','avg_order_value']
BENCH=['month_start','total_market_revenue','total_market_orders','total_market_customers','channel_count','market_avg_revenue','market_avg_orders','market_avg_aov','market_median_revenue','revenue_hhi']
MART=['month_start','channel','quarter','order_count','unique_customers','total_revenue','avg_order_value','prev_month_revenue','revenue_mom_change','revenue_mom_pct','growth_category','channel_tenure','rolling_3m_revenue','monthly_total_revenue','market_share_pct','market_share_change','channel_rank','prev_month_rank','rank_change','consecutive_growth_months','performance_tier','is_top_channel','ytd_revenue','ytd_order_count','pct_of_ytd_revenue','qtd_revenue','growth_acceleration','revenue_volatility','prev_month_customers','customer_growth_rate','market_avg_revenue','vs_market_revenue_pct','market_avg_aov','vs_market_aov_pct','aov_rank','revenue_hhi','growth_consistency_score','channel_momentum_score','channel_efficiency_score','strategic_recommendation']
def encoded(x):return json.dumps(x,default=str,sort_keys=True,indent=2)+'\n'
def digest(x):return hashlib.sha256(encoded(x).encode()).hexdigest()
def read_raw(c):return [dict(zip(RAW,r))for r in c.execute('SELECT '+','.join(RAW)+' FROM ORDERS.ORDERS ORDER BY order_id').fetchall()]
def derive(raw,first_month='emerging',rounding=rounding):
 if isinstance(raw,dict):raw=raw['orders']
 assert first_month in ['emerging','null']
 ties=[]
 def rd(x,col,key,places=2):
  if x is None:return None
  x=D(x);unit=D(1).scaleb(-places)
  if abs(x)%unit==unit/2:ties.append({'relation_key':key,'column':col,'exact':str(x),'places':places})
  return x.quantize(unit,rounding=rounding)
 def ratio(n,d,col,key):return rd(D(n)/D(d)*100,col,key)if d else None
 trim=lambda v:v.strip()if isinstance(v,str)else v
 selected=[r for r in raw if r['ordered_at']is not None and'2024-01-01'<=str(r['ordered_at'])<'2025-01-01'and r['status']is not None and trim(r['status'])not in ['CANCELLED','RETURNED']and r['order_source']is not None]
 staging=[{k:trim(r[k])if k!='ordered_at'else r[k]for k in STAGING}for r in selected];groups=collections.defaultdict(list)
 for r in staging:groups[(str(r['ordered_at'])[:7]+'-01',r['order_source'])].append(r)
 metrics={}
 for key,rs in sorted(groups.items()):
  vals=[D(r['grand_total'])for r in rs if r['grand_total']is not None];rev=rd(sum(vals,D(0)),'total_revenue',key)if vals else None;assert rev is not None
  metrics[key]=dict(zip(METRICS,[key[0],key[1],len(rs),len({r['customer_id']for r in rs if r['customer_id']is not None}),rev,rd(rev/len(rs),'avg_order_value',key)]))
 benchmarks={};monthly=collections.defaultdict(list)
 for key,r in metrics.items():monthly[key[0]].append(r)
 for month,rs in monthly.items():
  rev=sum(r['total_revenue']for r in rs);orders=sum(r['order_count']for r in rs);values=sorted(r['total_revenue']for r in rs);n=len(rs);median=values[n//2]if n%2 else(values[n//2-1]+values[n//2])/2;hhi=sum((r['total_revenue']/rev)**2 for r in rs)if rev else None
  benchmarks[month]=dict(zip(BENCH,[month,rev,orders,sum(r['unique_customers']for r in rs),n,rd(rev/n,'market_avg_revenue',month),rd(D(orders)/n,'market_avg_orders',month),rd(rev/orders,'market_avg_aov',month),rd(median,'market_median_revenue',month),rd(hhi,'revenue_hhi',month,4)]))
 ranks={key:1+sum(z['total_revenue']>r['total_revenue']for z in monthly[key[0]])for key,r in metrics.items()};aovranks={key:1+sum(z['avg_order_value']>r['avg_order_value']for z in monthly[key[0]])for key,r in metrics.items()}
 result=[];sequences={}
 for channel in sorted({key[1]for key in metrics}):
  seq=[r for key,r in sorted(metrics.items())if key[1]==channel];prev=None;streak=0;ytd=D(0);ytorders=0;qtotals=collections.defaultdict(lambda:D(0));past=[]
  for idx,r0 in enumerate(seq):
   r=r0.copy();month=r['month_start'];key=(month,channel);b=benchmarks[month];rev=r['total_revenue'];count=r['order_count'];cust=r['unique_customers'];tenure=idx+1;quarter='Q'+str((int(month[5:7])-1)//3+1);prior=prev['total_revenue']if prev else None;mom=ratio(rev-prior,prior,'revenue_mom_pct',key)if prior is not None else None;share=ratio(rev,b['total_market_revenue'],'market_share_pct',key);assert share is not None
   growth=None if mom is None else'Explosive'if mom>=100 else'Strong Growth'if mom>=50 else'Moderate Growth'if mom>=20 else'Slight Growth'if mom>0 else'Stable'if mom==0 else'Slight Decline'if mom>-20 else'Moderate Decline'if mom>-50 else'Sharp Decline'
   streak=streak+1 if mom is not None and mom>0 else 0
   # Explicit Emerging clause retained as one reading; conflicting final first-month-NULL sentence investigated separately.
   tier=('Emerging'if first_month=='emerging'else None)if growth is None and tenure<=2 else None if growth is None else'Market Leader'if share>=40 and growth in ['Explosive','Strong Growth','Moderate Growth']else'Strong Performer'if 25<=share<40 and growth not in ['Sharp Decline','Moderate Decline']else'Growth Potential'if share<25 and growth in ['Explosive','Strong Growth']else'Stable Core'if growth in ['Stable','Slight Growth','Slight Decline']and share>=10 else'At Risk'if growth in ['Moderate Decline','Sharp Decline']else'Niche'
   ytd+=rev;ytorders+=count;qtotals[quarter]+=rev;last3=[z['total_revenue']for z in seq[max(0,idx-2):idx+1]];mean=sum(last3)/len(last3);vol=rd((sum((z-mean)**2 for z in last3)/len(last3)).sqrt()/mean*100,'revenue_volatility',key)if tenure>=3 and mean else None
   cg=ratio(cust-prev['unique_customers'],prev['unique_customers'],'customer_growth_rate',key)if prev else None;consistency=sum(z['revenue_mom_pct']is not None and z['revenue_mom_pct']>0 for z in past[-5:])+(mom is not None and mom>0);rank=ranks[key];aovrank=aovranks[key]
   momentum=(20 if mom is None else 40 if mom>=50 else 30 if mom>=20 else 20 if mom>=0 else 10 if mom>=-20 else 0)+(35 if share>=40 else 28 if share>=25 else 21 if share>=15 else 14 if share>=10 else 7)+({1:25,2:20,3:15,4:10}.get(rank,5))
   efficiency={1:40,2:32,3:24,4:16}.get(aovrank,8)+(21 if cg is None else 35 if cg>=50 else 28 if cg>=20 else 21 if cg>=0 else 14 if cg>=-20 else 7)+(25 if consistency>=5 else 20 if consistency>=4 else 15 if consistency>=3 else 10 if consistency>=2 else 5)
   action='Invest Heavily'if momentum>=80 and efficiency>=70 and streak>=3 else'Scale Up'if tier=='Market Leader'or(momentum>=70 and share>=20)else'Optimize'if tier in ['Strong Performer','Stable Core']and momentum>=50 and efficiency>=50 else'Experiment'if tier=='Growth Potential'or(tier=='Emerging'and momentum>=40)else'Maintain'if tier in ['Stable Core','Niche']and streak>=1 else'Divest'if tier=='At Risk'and streak==0 and momentum<20 and efficiency<30 else'Review'if tier=='At Risk'or momentum<30 else'Monitor'
   r.update(quarter=quarter,prev_month_revenue=prior,revenue_mom_change=rd(rev-prior,'revenue_mom_change',key)if prior is not None else None,revenue_mom_pct=mom,growth_category=growth,channel_tenure=tenure,rolling_3m_revenue=rd(mean,'rolling_3m_revenue',key)if tenure>=3 else None,monthly_total_revenue=b['total_market_revenue'],market_share_pct=share,market_share_change=rd(share-prev['market_share_pct'],'market_share_change',key)if prev else None,channel_rank=rank,prev_month_rank=prev['channel_rank']if prev else None,rank_change=prev['channel_rank']-rank if prev else None,consecutive_growth_months=streak,performance_tier=tier,is_top_channel='Y'if rank==1 else'N',ytd_revenue=ytd,ytd_order_count=ytorders,pct_of_ytd_revenue=ratio(rev,ytd,'pct_of_ytd_revenue',key),qtd_revenue=qtotals[quarter],growth_acceleration=rd(mom-prev['revenue_mom_pct'],'growth_acceleration',key)if mom is not None and prev and prev['revenue_mom_pct']is not None else None,revenue_volatility=vol,prev_month_customers=prev['unique_customers']if prev else None,customer_growth_rate=cg,market_avg_revenue=b['market_avg_revenue'],vs_market_revenue_pct=ratio(rev-b['market_avg_revenue'],b['market_avg_revenue'],'vs_market_revenue_pct',key),market_avg_aov=b['market_avg_aov'],vs_market_aov_pct=ratio(r['avg_order_value']-b['market_avg_aov'],b['market_avg_aov'],'vs_market_aov_pct',key),aov_rank=aovrank,revenue_hhi=b['revenue_hhi'],growth_consistency_score=int(consistency),channel_momentum_score=momentum,channel_efficiency_score=efficiency,strategic_recommendation=action);assert set(r)==set(MART);result.append(r);past.append(r);prev=r
  sequences[channel]=[{'month':r['month_start'],'growth':r['revenue_mom_pct'],'streak':r['consecutive_growth_months'],'tier':r['performance_tier']}for r in past]
 outputs={'main.stg_orders__channel':staging,'main.int_monthly_channel_metrics':list(metrics.values()),'main.int_monthly_market_benchmarks':list(benchmarks.values()),'main_channel_analytics.monthly_channel_performance':sorted(result,key=lambda r:(r['month_start'],r['channel_rank'],r['channel']))}
 return {'outputs':outputs,'exact_rounding_ties':ties,'profiles':{'raw_orders':len(raw),'eligible_orders':len(staging),'months':len(benchmarks),'channel_months':len(result),'channels':len(sequences),'eligible_null_grand_total':sum(r['grand_total']is None for r in staging),'trim_changes':sum(any(r[k]!=r[k].strip()for k in ['order_id','customer_id','order_source']if isinstance(r[k],str))for r in selected),'sequences':sequences},'scope':'Independent raw-order filter/Python grouping/Decimal arithmetic/window arrays. Correct positive-growth streak reset. First-month Emerging is one explicit clause; conflicting first-monthNULL sentence not silently resolved. Exact half-cent ties separately listed.'}
