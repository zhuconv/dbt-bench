"""Phase4 correction: coefficient of variation uses unrounded mean as task explicitly states. Legacy derivation is retained unchanged."""
# Reviewed imperative replay adapted from existing original-input deriver.
# Original source SHA256: fbbad34f63a044967776a8bc5b7ddafcb93cc62260b05dffa53e271c2875d0c5
"""CLV business arithmetic from the explicitly provided staging inputs."""
from collections import defaultdict,Counter
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP,ROUND_HALF_DOWN
import hashlib
import json

def roundd(x,n=2):return Decimal(x).quantize(Decimal(1).scaleb(-n),rounding=ROUND_HALF_UP)
def digest(v):return hashlib.sha256(json.dumps(v,default=str,separators=(',',':')).encode()).hexdigest()

def derive(data, rounding=ROUND_HALF_UP, fallbacks=None):
    def roundd(x,n=2):return Decimal(x).quantize(Decimal(1).scaleb(-n),rounding=rounding)
    sql='SELECT order_id,customer_id,ordered_at,grand_total,status,test_order_flag FROM main.stg_orders__orders ORDER BY order_id'
    raw=[tuple(r[c]for c in ['order_id','customer_id','ordered_at','grand_total','status','test_order_flag'])for r in sorted(data['orders'],key=lambda r:(r['order_id']is None,r['order_id']or''))];customer_sql='SELECT customer_id,first_name,last_name FROM main.stg_customer__customers ORDER BY customer_id';customer_rows=[tuple(r[c]for c in ['customer_id','first_name','last_name'])for r in sorted(data['customers'],key=lambda r:(r['customer_id']is None,r['customer_id']or''))];names={cid:(' '.join([(first or ''),(last or '')]).strip() or 'Customer '+cid) for cid,first,last in customer_rows};groups=defaultdict(list)
    for oid,cid,stamp,money,status,test in raw:
        if stamp is not None and date(2023,1,1)<=stamp.date()<=date(2024,11,30) and status is not None and status not in ['CANCELLED','RETURNED','FAILED'] and test not in [True,1]:
            assert cid is not None,'Qualifying NULL customer requires explicit scope decision';groups[cid].append((oid,stamp,money))
    eligible={cid:sorted(rows,key=lambda x:(x[1],x[0])) for cid,rows in groups.items() if len(rows)>=2};output=[];aux={};notes=[];monthly=[]
    for cid,rows in sorted(eligible.items()):
        n=len(rows);gaps=[Decimal((b[1].date()-a[1].date()).days) for a,b in zip(rows,rows[1:])];first=rows[0][1];last=rows[-1][1];total=roundd(sum(x[2] for x in rows));avg_money=roundd(total/n);avg_gap=roundd(sum(gaps)/len(gaps),1);orders_year=roundd(Decimal(365)/avg_gap) if avg_gap else Decimal('0.00');annual=roundd(orders_year*avg_money);clv=roundd(annual*3);npv=roundd(annual*Decimal('2.7833'));tenure=(date(2024,12,1)-first.date()).days;recency=(date(2024,12,1)-last.date()).days;at_risk=Decimal(recency)>2*avg_gap;half=(n+1)//2;a=sum(x[2] for x in rows[:half])/half;b=sum(x[2] for x in rows[half:])/(n-half);trajectory='Accelerating' if b>a*Decimal('1.1') else 'Decelerating' if b<a*Decimal('.9') else 'Stable'
        if n<4:velocity='Insufficient Data'
        else:
            recent=sum(gaps[-2:])/2;historical=sum(gaps[:-2])/len(gaps[:-2]);velocity='Accelerating' if recent<historical*Decimal('.8') else 'Slowing' if recent>historical*Decimal('1.2') else 'Stable'
        recency_score=Decimal(0) if recency<=avg_gap else min(Decimal(40),(Decimal(recency)-avg_gap)/avg_gap*20) if avg_gap else Decimal(40);frequency_score=30 if orders_year<1 else 20 if orders_year<2 else 10 if orders_year<4 else 0;trend_score=30 if velocity=='Slowing' else 0 if velocity=='Accelerating' else 15;churn=roundd(min(100,recency_score+frequency_score+trend_score),1);segment='Platinum' if clv>=5000 else 'Gold' if clv>=2000 else 'Silver' if clv>=500 else 'Bronze';stage='New' if n==2 and tenure<=90 else 'Growing' if n>=3 and orders_year>4 and not at_risk and trajectory!='Decelerating' else 'Mature' if n>=3 and orders_year<=4 and not at_risk and trajectory!='Decelerating' else 'At Risk - High Value' if at_risk and recency<=365 and segment in ['Platinum','Gold'] else 'Declining' if at_risk and recency<=365 else 'Churned' if recency>365 else None
        if stage is None:
            notes.append({'customer_id':cid,'rule':'lifecycle_stage','reason':'No enumerated lifecycle condition matches; original input proves default eligibility.'})
            stage=(fallbacks or {}).get(cid,'Declining')
        expected_date=last.date()+timedelta(days=int(roundd(avg_gap,0)));until=(expected_date-date(2024,12,1)).days
        std=(sum((g-sum(gaps)/len(gaps))**2 for g in gaps)/(len(gaps)-1)).sqrt() if len(gaps)>=2 else None;consistency=max(Decimal(0),100-std/(sum(gaps)/len(gaps))*100) if n>=3 and avg_gap else Decimal(50)
        row={'customer_id':cid,'customer_name':names.get(cid,'Customer '+cid),'first_order_date':first,'last_order_date':last,'customer_tenure_days':tenure,'days_since_last_order':recency,'total_orders':n,'total_revenue':total,'avg_order_value':avg_money,'avg_days_between_orders':avg_gap,'orders_per_year':orders_year,'predicted_annual_revenue':annual,'clv_3_year':clv,'clv_3_year_npv':npv,'clv_segment':segment,'is_at_risk':at_risk,'clv_percentile':None,'clv_quartile':None,'lifecycle_stage':stage,'first_order_quarter':f'{first.year}-Q{(first.month-1)//3+1}','value_trajectory':trajectory,'velocity_trend':velocity,'churn_probability_score':churn,'revenue_contribution_pct':None,'cumulative_revenue_pct':None,'revenue_rank':None,'engagement_score':None,'expected_next_order_date':expected_date,'days_until_expected_order':until,'days_overdue':max(0,-until)};output.append(row);aux[cid]={'consistency':consistency}
        months=defaultdict(list)
        for oid,stamp,money in rows:months[stamp.strftime('%Y-%m')].append(money)
        cum_orders=0;cum_money=Decimal(0);first_month=first.year*12+first.month
        for rank,(month,money_values) in enumerate(sorted(months.items()),1):
            year,mon=map(int,month.split('-'));offset=year*12+mon-first_month;money=roundd(sum(money_values));cum_money+=money;cum_orders+=len(money_values);monthly.append({'customer_id':cid,'order_month':month,'monthly_orders':len(money_values),'monthly_revenue':money,'cumulative_orders':cum_orders,'cumulative_revenue':cum_money,'months_since_first_order':offset,'avg_monthly_revenue':roundd(cum_money/(offset+1)),'is_active_month':True,'order_month_rank':rank})
    segments=defaultdict(list)
    for row in output:segments[row['clv_segment']].append(row)
    ties=[]
    for segment,rows in segments.items():
        rows.sort(key=lambda r:(r['clv_3_year'],r['customer_id']));q,rem=divmod(len(rows),4)
        for i,row in enumerate(rows):
            first_i=next(j for j,r in enumerate(rows) if r['clv_3_year']==row['clv_3_year']);row['clv_percentile']=roundd(Decimal(first_i)*100/(len(rows)-1),1) if len(rows)>1 else Decimal('0.0');row['clv_quartile']=1+i//(q+1) if i<(q+1)*rem else 1+rem+(i-(q+1)*rem)//q
        ties.extend({'segment':segment,'clv':str(value),'customers':count} for value,count in Counter(r['clv_3_year'] for r in rows).items() if count>1)
    total_revenue=sum(r['total_revenue'] for r in output);cumulative=Decimal(0);revenue_values=sorted({r['total_revenue'] for r in output},reverse=True)
    for row in sorted(output,key=lambda r:(-r['total_revenue'],r['customer_id'])):
        pct=roundd(row['total_revenue']*100/total_revenue,1);cumulative+=pct;row['revenue_contribution_pct']=pct;row['cumulative_revenue_pct']=cumulative;row['revenue_rank']=revenue_values.index(row['total_revenue'])+1;recency=max(Decimal(0),100-Decimal(row['days_since_last_order'])/Decimal('3.65'));freq=min(100,row['orders_per_year']*20);row['engagement_score']=roundd(Decimal('.30')*recency+Decimal('.30')*freq+Decimal('.25')*row['clv_percentile']+Decimal('.15')*aux[row['customer_id']]['consistency'],1)
    summary=[];cohort=[]
    def average(rows,col,n=2):return roundd(sum(r[col] for r in rows)/len(rows),n)
    def count(rows,col,value=True):return sum(r[col]==value for r in rows)
    for segment,rows in segments.items():
        n=len(rows);revenue=sum(r['total_revenue'] for r in rows);risk=count(rows,'is_at_risk');summary.append({'clv_segment':segment,'customer_count':n,'total_revenue':revenue,'avg_clv_3_year':average(rows,'clv_3_year'),'avg_clv_3_year_npv':average(rows,'clv_3_year_npv'),'avg_orders_per_year':average(rows,'orders_per_year'),'at_risk_count':risk,'at_risk_percentage':roundd(Decimal(risk)*100/n,1),'churned_count':count(rows,'lifecycle_stage','Churned'),'avg_tenure_days':roundd(Decimal(sum(r['customer_tenure_days'] for r in rows))/n,1),'avg_engagement_score':average(rows,'engagement_score',1),'avg_churn_probability':average(rows,'churn_probability_score',1),'accelerating_count':count(rows,'value_trajectory','Accelerating'),'decelerating_count':count(rows,'value_trajectory','Decelerating'),'pct_revenue_contribution':roundd(revenue*100/total_revenue,1)})
    cohorts=defaultdict(list)
    for row in output:cohorts[row['first_order_quarter']].append(row)
    for quarter,rows in sorted(cohorts.items()):
        n=len(rows);cohort.append({'cohort_quarter':quarter,'cohort_size':n,'total_cohort_revenue':sum(r['total_revenue'] for r in rows),'avg_clv_3_year':average(rows,'clv_3_year'),'avg_clv_3_year_npv':average(rows,'clv_3_year_npv'),'avg_orders':roundd(Decimal(sum(r['total_orders'] for r in rows))/n),'retention_rate':roundd(Decimal(n-count(rows,'lifecycle_stage','Churned'))*100/n,1),'at_risk_rate':roundd(Decimal(count(rows,'is_at_risk'))*100/n,1),'avg_engagement_score':average(rows,'engagement_score',1),'avg_churn_probability':average(rows,'churn_probability_score',1),**{seg.lower()+'_count':count(rows,'clv_segment',seg) for seg in ['Platinum','Gold','Silver','Bronze']},'accelerating_pct':roundd(Decimal(count(rows,'value_trajectory','Accelerating'))*100/n,1),'decelerating_pct':roundd(Decimal(count(rows,'value_trajectory','Decelerating'))*100/n,1)})
    output_tables={'main.clv_segments':output,'main.clv_segment_summary':sorted(summary,key=lambda r:-r['avg_clv_3_year']),'main.clv_cohort_analysis':cohort,'main.clv_monthly_trends':monthly};profile={'raw_sql':sql,'raw_rows':len(raw),'raw_sha256':digest(raw),'customer_sql':customer_sql,'customer_rows':len(customer_rows),'customer_sha256':digest(customer_rows),'qualifying_customers_before_minimum':len(groups),'eligible_customers':len(eligible),'qualifying_orders':sum(len(r) for r in groups.values()),'eligible_orders':sum(len(r) for r in eligible.values()),'single_order_customer_ids':sorted(cid for cid,r in groups.items() if len(r)==1),'undefined_conditions':notes,'clv_ties':ties,'scope':'All four enumerated final marts independently calculated in Python from the provided staging inputs. Two-order customer eligibility applied to all final outputs. Unspecified NTILE ties and lifecycle fallback cases are recorded, not certified.'}

    # Independently reconstruct every explicitly described intermediate concept.
    intermediate_orders=[];intermediate_metrics=[]
    final_by_id={r['customer_id']:r for r in output}
    for cid,rows in sorted(eligible.items()):
        gaps=[Decimal((b[1].date()-a[1].date()).days) for a,b in zip(rows,rows[1:])];n=len(rows);half=(n+1)//2
        for index,(oid,stamp,money) in enumerate(rows):
            source=next(r for r in data['orders'] if r['order_id']==oid)
            intermediate_orders.append({'order_id':oid,'customer_id':cid,'ordered_at':stamp,'grand_total':money,'status':source['status'],
                'order_sequence':index+1,'customer_order_count':n,'previous_ordered_at':rows[index-1][1] if index else None,
                'days_since_previous_order':int(gaps[index-1]) if index else None})
        final=final_by_id[cid]
        metric={k:final[k] for k in 'customer_id customer_name first_order_date last_order_date total_orders total_revenue avg_days_between_orders customer_tenure_days days_since_last_order first_order_quarter avg_order_value'.split()}
        metric.update(avg_days_between_orders_raw=sum(gaps)/len(gaps),
            stddev_days_between_orders=(sum((g-sum(gaps)/len(gaps))**2 for g in gaps)/(len(gaps)-1)).sqrt() if len(gaps)>=2 else None,
            first_half_avg=roundd(sum(x[2] for x in rows[:half])/half),second_half_avg=roundd(sum(x[2] for x in rows[half:])/(n-half)),
            recent_avg_days=sum(gaps[-2:])/2 if n>=4 else None,
            historical_avg_days=sum(gaps[:-2])/len(gaps[:-2]) if n>=4 else None)
        intermediate_metrics.append(metric)
    output_tables['main.int_clv_customer_orders']=intermediate_orders
    output_tables['main.int_clv_metrics']=intermediate_metrics
    output_tables['main.clv_predictions']=[dict(r) for r in output]
    profile['rounding']=rounding
    return {'outputs':output_tables,'profile':profile,'policy':{'rounding':rounding,'lifecycle_fallback':'unresolved'}}
