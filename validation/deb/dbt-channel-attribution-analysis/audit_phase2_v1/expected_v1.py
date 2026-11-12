"""Independent Python attribution from original orders/channels; no candidate SQL/gold."""
from collections import defaultdict
from datetime import date,datetime
from decimal import Decimal as D,ROUND_HALF_UP
STAGING=('order_id','customer_id','channel_id','channel_name','channel_type','order_date','order_month','grand_total')
FIRST=('customer_id','first_channel_id','first_channel_name','first_order_date','first_order_value','total_orders','total_spend','distinct_channels_used','is_cross_channel','customer_lifespan_days')
MART=('channel_id','channel_name','channel_type','total_orders','total_revenue','unique_customers','new_customers_acquired','avg_order_value','orders_per_customer','revenue_per_customer','revenue_share_pct','order_share_pct','customer_share_pct','revenue_rank','channel_efficiency','acquisition_strength','channel_index','cross_channel_customers','cross_channel_rate','avg_customer_lifespan','channel_engagement_score','engagement_tier')
COLS={'stg_orders__channels':STAGING,'int_customer_channel_first_touch':FIRST,'channel_performance':MART}
KEYS=dict(zip(COLS,['order_id','customer_id','channel_id']))
def day(v):return v.date()if isinstance(v,datetime)else v
def clean(v):return v.strip()if isinstance(v,str)else v
def rounded(v,n=2):return D(v).quantize(D(1).scaleb(-n),rounding=ROUND_HALF_UP)
def ratio(a,b):return rounded(D(a)/D(b))if b else D(0)
def derive(raw):
    channels={r['channel_id']:r for r in raw['channels']};stg=[];by=defaultdict(list);facts={'null_status_in_window':0,'unmatched_nonnull_channel_in_window':0};math={}
    for r in raw['orders']:
        d=day(r['ordered_at'])
        if d is None or not date(2024,1,1)<=d<date(2025,1,1):continue
        if r['status']is None:facts['null_status_in_window']+=1
        if r['channel_id']is not None and r['channel_id']not in channels:facts['unmatched_nonnull_channel_in_window']+=1
        if r['status']is None or clean(r['status'])in {'CANCELLED','RETURNED','FAILED'}or r['channel_id']not in channels:continue
        c=channels[r['channel_id']];x={k:clean(r[k])for k in ['order_id','customer_id','channel_id']};x.update(channel_name=clean(c['channel_name']),channel_type=clean(c['channel_type']),order_date=d,order_month=d.replace(day=1),grand_total=r['grand_total']);stg.append(x);by[x['customer_id']].append(x)
    first=[];ties=0
    for cid,rs in by.items():
        rs=sorted(rs,key=lambda r:(r['order_date'],r['order_id']));a=rs[0];n=len({r['channel_id']for r in rs});ties+=sum(r['order_date']==a['order_date']for r in rs)>1;spend=sum((r['grand_total']for r in rs),D(0));math[('int_customer_channel_first_touch',cid,'total_spend')]=spend
        first.append(dict(customer_id=cid,first_channel_id=a['channel_id'],first_channel_name=a['channel_name'],first_order_date=a['order_date'],first_order_value=a['grand_total'],total_orders=len(rs),total_spend=rounded(spend),distinct_channels_used=n,is_cross_channel='Y'if n>1 else'N',customer_lifespan_days=(rs[-1]['order_date']-rs[0]['order_date']).days))
    mart=[]
    for cid in sorted({r['channel_id']for r in stg}):
        rs=[r for r in stg if r['channel_id']==cid];acq=[r for r in first if r['first_channel_id']==cid];rev=sum((r['grand_total']for r in rs),D(0));revenue=rounded(rev);nu=len({r['customer_id']for r in rs});nc=sum(r['is_cross_channel']=='Y'for r in acq);math[('channel_performance',cid,'total_revenue')]=rev
        mart.append(dict(channel_id=cid,channel_name=rs[0]['channel_name'],channel_type=rs[0]['channel_type'],total_orders=len(rs),total_revenue=revenue,unique_customers=nu,new_customers_acquired=len(acq),avg_order_value=ratio(revenue,len(rs)),orders_per_customer=ratio(len(rs),nu),revenue_per_customer=ratio(revenue,nu),cross_channel_customers=nc,cross_channel_rate=ratio(100*nc,len(acq)),avg_customer_lifespan=ratio(sum(r['customer_lifespan_days']for r in acq),len(acq))))
    total_orders=sum(r['total_orders']for r in mart);revenue=sum((r['total_revenue']for r in mart),D(0));overall=revenue/D(total_orders)if total_orders else D(0);shares=[]
    for r in mart:
        cid=r['channel_id'];r.update(revenue_share_pct=ratio(100*r['total_revenue'],revenue),order_share_pct=ratio(100*r['total_orders'],total_orders),customer_share_pct=ratio(100*r['new_customers_acquired'],len(first)),revenue_rank=1+len({v['total_revenue']for v in mart if v['total_revenue']>r['total_revenue']}));r['channel_efficiency']='High Efficiency'if r['avg_order_value']>=D('1.2')*overall else'Average Efficiency'if r['avg_order_value']>=D('.8')*overall else'Low Efficiency';r['acquisition_strength']='Primary Acquisition'if r['customer_share_pct']>=40 else'Secondary Acquisition'if r['customer_share_pct']>=20 else'Supplementary';r['channel_index']=ratio(r['revenue_share_pct'],r['order_share_pct']);shares.append(r['revenue_share_pct'])
    components=[]
    for r in mart:
        percentile=D(sum(v<r['revenue_share_pct']for v in shares))/D(len(shares)-1)if len(shares)>1 else D(0);rp=int(rounded(percentile*30,0));a=r['customer_share_pct'];ap=25 if a>=50 else 20 if a>=30 else 15 if a>=15 else 10 if a>=5 else 5;c=r['cross_channel_rate'];cp=25 if c>=40 else 20 if c>=25 else 15 if c>=15 else 10 if c>=5 else 5;l=r['orders_per_customer'];lp=20 if l>=3 else 15 if l>=2 else 10 if l>=D('1.5')else 5;r['channel_engagement_score']=max(5,min(100,rp+ap+cp+lp));s=r['channel_engagement_score'];r['engagement_tier']='Elite'if s>=80 else'Strong'if s>=60 else'Moderate'if s>=40 else'Emerging';components.append(dict(channel_id=r['channel_id'],revenue=rp,acquisition=ap,cross_channel=cp,loyalty=lp))
    mart.sort(key=lambda r:r['total_revenue'],reverse=True)
    facts.update(eligible_orders=len(stg),eligible_customers=len(first),channels=len(mart),first_date_tied_customers=ties,distinct_revenue_shares=len(set(shares)),engagement_components=components,rounding_half_ties=[{'relation':rel,'key':key,'column':col,'exact':str(v)}for(rel,key,col),v in math.items()if abs(v*100)%1==D('.5')])
    out={n:[{c:r[c]for c in cols}for r in rows]for(n,cols),rows in zip(COLS.items(),[stg,first,mart])}
    return out,facts
