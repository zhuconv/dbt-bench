"""Independent raw-row derivation of the explicitly stated order-interval rules."""
from collections import defaultdict, Counter
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json


def rounded(x, places=2):
    return Decimal(x).quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP)


def derive_intervals(con):
    sql='SELECT order_id,customer_id,ordered_at,grand_total,status FROM main.orders ORDER BY order_id'
    raw=con.execute(sql).fetchall();staging=[];grouped=defaultdict(list)
    for oid,cid,stamp,money,status in raw:
        if stamp is None or not date(2024,1,1)<=stamp.date()<=date(2024,12,31) or status is None or status.strip() in {'CANCELLED','RETURNED','FAILED'}:continue
        assert cid is not None,'NULL qualifying customer needs explicit adjudication'
        row={'order_id':oid.strip(),'customer_id':cid.strip(),'order_date':stamp.date(),'grand_total':money};staging.append(row);grouped[cid.strip()].append(row)
    intermediate=[];metrics=[];notes=[]
    for cid,rows in sorted(grouped.items()):
        rows.sort(key=lambda r:(r['order_date'],r['order_id']));count=len(rows);half=(count+1)//2;gaps=[];half_gaps=[[],[]];half_values=[[],[]]
        for i,row in enumerate(rows):
            prior=rows[i-1]['order_date'] if i else None;gap=(row['order_date']-prior).days if prior else None;side=0 if i<half else 1
            if gap is not None:gaps.append(Decimal(gap));half_gaps[side].append(Decimal(gap))
            half_values[side].append(row['grand_total'])
            intermediate.append({'customer_id':cid,'order_date':row['order_date'],'grand_total':row['grand_total'],'order_sequence':i+1,'prev_order_date':prior,'days_since_prev_order':gap,'order_half':['first_half','second_half'][side]})
        first=rows[0]['order_date'];last=rows[-1]['order_date'];lifespan=(last-first).days;recency=(date(2024,12,31)-last).days;revenue=rounded(sum(r['grand_total'] for r in rows));avg_gap=rounded(sum(gaps)/len(gaps)) if gaps else None
        stdev=rounded((sum((x-sum(gaps)/len(gaps))**2 for x in gaps)/(len(gaps)-1)).sqrt()) if len(gaps)>=2 else None
        frequency='One-time' if count==1 else 'Frequent' if avg_gap<30 else 'Regular' if avg_gap<90 else 'Occasional' if avg_gap<180 else 'Rare'
        if stdev is None:consistency=None
        elif avg_gap==0:consistency=None;notes.append({'customer_id':cid,'rule':'purchase_consistency','reason':'Zero mean gap gives undefined CV; instruction does not define default.'})
        else:
            cv=stdev/avg_gap;consistency='High' if cv<Decimal('.5') else 'Medium' if cv<1 else 'Low'
        acceleration=None
        if count>=4:
            a=sum(half_gaps[0])/len(half_gaps[0]);b=sum(half_gaps[1])/len(half_gaps[1]);acceleration='Accelerating' if b<a*Decimal('.8') else 'Decelerating' if b>a*Decimal('1.2') else 'Stable'
        spending=None
        if count>1:
            a=sum(half_values[0])/len(half_values[0]);b=sum(half_values[1])/len(half_values[1]);spending='Increasing' if b>a*Decimal('1.1') else 'Decreasing' if b<a*Decimal('.9') else 'Stable'
        churn='High' if recency>=120 or recency>=60 and count==1 else 'Medium' if recency>=60 else 'Low'
        tenure=next(points for threshold,points in [(300,30),(200,24),(100,18),(50,12),(1,6),(0,0)] if lifespan>=threshold)
        repeat=next(points for threshold,points in [(20,40),(10,32),(5,24),(3,16),(2,8),(1,0)] if count>=threshold)
        loyalty=tenure+repeat+{'High':30,'Medium':20,'Low':10,None:5}[consistency]
        momentum='Inactive' if recency>=90 else 'Emerging' if count<4 and recency<60 else 'Accelerating' if acceleration=='Accelerating' and spending in ['Increasing','Stable'] else 'Stable' if acceleration=='Stable' and spending=='Stable' else 'Decelerating' if acceleration=='Decelerating' or spending=='Decreasing' else None
        if momentum is None:notes.append({'customer_id':cid,'rule':'engagement_momentum','reason':'None of the enumerated conditions matches; instruction supplies no fallback label.'})
        lifecycle='Churned' if recency>=120 else 'Declining' if lifespan>=90 and 60<=recency<120 else 'New' if count<=2 and lifespan<60 else 'Growing' if count>=3 and lifespan<180 and recency<60 else 'Mature' if lifespan>=180 and recency<90 else None
        if lifecycle is None:notes.append({'customer_id':cid,'rule':'lifecycle_stage','reason':'None of the first-match conditions matches; instruction supplies no fallback label.'})
        metrics.append({'customer_id':cid,'total_orders':count,'first_order_date':first,'last_order_date':last,'customer_lifespan_days':lifespan,'total_revenue':revenue,'avg_order_value':rounded(revenue/count),'avg_days_between_orders':avg_gap,'min_days_between_orders':int(min(gaps)) if gaps else None,'max_days_between_orders':int(max(gaps)) if gaps else None,'std_dev_days':stdev,'ordering_frequency':frequency,'is_repeat_customer':'Y' if count>1 else 'N','days_since_last_order':recency,'customer_tier':None,'churn_risk':churn,'purchase_consistency':consistency,'customer_value_score':None,'customer_segment':None,'order_acceleration':acceleration,'monthly_order_rate':rounded(Decimal(count)*30/max(Decimal(30),Decimal(lifespan))),'spending_trend':spending,'loyalty_index':loyalty,'engagement_momentum':momentum,'lifecycle_stage':lifecycle})
    ranked=sorted(metrics,key=lambda r:(r['total_revenue'],r['customer_id']));n=len(ranked);q,rem=divmod(n,4);money_counts=Counter(r['total_revenue'] for r in ranked)
    for i,row in enumerate(ranked):
        quartile=1+i//(q+1) if i<(q+1)*rem else 1+rem+(i-(q+1)*rem)//q
        row['customer_tier']=['Bronze','Silver','Gold','Platinum'][quartile-1]
        rank=next(j for j,r in enumerate(ranked) if r['total_revenue']==row['total_revenue'])
        revenue_points=int(rounded(Decimal(rank)*40/(n-1),0)) if n>1 else 0;frequency_points={'Frequent':30,'Regular':20,'Occasional':10,'Rare':5,'One-time':5}[row['ordering_frequency']];recency_points=int(rounded(max(Decimal(0),30-Decimal(row['days_since_last_order'])*30/365),0));score=revenue_points+frequency_points+recency_points
        row['customer_value_score']=score;row['customer_segment']='Champion' if score>=80 else 'Loyal' if score>=60 else 'Potential' if score>=40 else 'At Risk' if score>=20 else 'Hibernating'
    tied=[{'total_revenue':str(money),'customers':count} for money,count in money_counts.items() if count>1]
    metadata={'raw_sql':sql,'raw_rows':len(raw),'raw_sha256':hashlib.sha256(json.dumps(raw,default=str,separators=(',',':')).encode()).hexdigest(),'filtered_orders':len(staging),'customers':len(metrics),'executed_undefined_rule_cases':notes,'revenue_ties':tied,'scope':'Raw Python arithmetic, sample standard deviation and ordered thresholds. Undefined fallback labels are explicitly inconclusive and not silently borrowed from the reference. Source enterprise_db.ORDERS must separately bind to main.ORDERS.'}
    return {'main_order_analytics.stg_orders__timeline':staging,'main_order_analytics.int_customer_order_gaps':intermediate,'main_order_analytics.customer_order_intervals':sorted(metrics,key=lambda r:-r['total_revenue'])},metadata
