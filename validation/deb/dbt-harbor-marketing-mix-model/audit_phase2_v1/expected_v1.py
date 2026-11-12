"""Bounded source-only arithmetic from reviewed previous independent implementation. No tested checker or submitted output read. Unknown default aliases/calendar branches stay explicitly scoped."""
import collections,datetime,decimal,math
D=decimal.Decimal
def mmm_expected(raw):
 queries={'sales':'SELECT date_key,channel_key,total_amount,order_id,quantity FROM main.FACT_SALES','spend':'SELECT campaign_id,date_key,spend_amount,impressions,clicks FROM main.FACT_MARKETING_SPEND','campaign_channels':'SELECT campaign_id,channel_type FROM main.CAMPAIGN_CHANNELS','dates':'SELECT date_key,full_date,day_of_week,day_name,month_number,quarter,is_weekend,is_holiday FROM main.DIM_DATE','channels':'SELECT channel_key,channel_name FROM main.DIM_CHANNEL'}
 dates={r['date_key']:r for r in raw['dates']};channelnames={r['channel_key']:r['channel_name']for r in raw['channels']};campaign=collections.defaultdict(list)
 for r in raw['campaign_channels']:campaign[r['campaign_id']].append(r['channel_type'])
 def classify(x):
  x=(x or '').lower()
  if 'email'in x:return 'Email'
  if 'google'in x or 'search'in x:return 'Paid Search'
  if any(w in x for w in ['facebook','instagram','social','twitter','linkedin','sms']):return 'Social Media'
  if 'display'in x:return 'Display Ads'
  if 'tv'in x or 'video'in x:return 'TV/Video'
  return 'Display Ads'
 spend=collections.defaultdict(lambda:[D(0),0,0]);sales=collections.defaultdict(D);sales_detail=collections.defaultdict(list)
 for r in raw['spend']:
  if r['date_key']not in dates:continue
  for ch in campaign[r['campaign_id']]:
   day=dates[r['date_key']]['full_date'];v=spend[day,classify(ch)];v[0]+=r['spend_amount']or 0;v[1]+=r['impressions']or 0;v[2]+=r['clicks']or 0
 for r in raw['sales']:
  if r['date_key']not in dates:continue
  day=dates[r['date_key']]['full_date'];sales[day]+=r['total_amount']or 0;sales_detail[day].append(r)
 spend_rows=[dict(metric_date=day,channel_name=ch,spend_amount=v[0],impressions=v[1],clicks=v[2])for(day,ch),v in spend.items()];daily_spend=collections.defaultdict(D)
 for (day,ch),v in spend.items():daily_spend[day]+=v[0]
 lo=max(min(sales),min(daily_spend));hi=min(max(sales),max(daily_spend));low_all=[v for day,v in sales.items()if daily_spend.get(day,D(0))<5000];low_overlap=[v for day,v in sales.items()if lo<=day<=hi and daily_spend.get(day,D(0))<5000];base_all=sum(low_all)/len(low_all) if low_all else None;base_overlap=sum(low_overlap)/len(low_overlap)if low_overlap else None;calendar=[r for r in raw['dates']if lo<=r['full_date']<=hi];baseline=[];mf=[.90,.92,.95,.98,1,1.02,1.03,1.02,1,1.03,1.05,1.2]
 for r in calendar:
  day=r['full_date'];weekday=day.weekday();dayfactor=.85 if weekday==0 else 1.05 if weekday==4 else .95 if weekday>=5 else 1;monthfactor=mf[day.month-1];holidayfactor=1.15 if r['is_holiday']else 1;months=(day.year-lo.year)*12+day.month-lo.month;trend=1.02**months;factor=trend*dayfactor*monthfactor*holidayfactor;baseline.append({'metric_date':day,'baseline_revenue':float(base_all)*factor if base_all is not None else None,'baseline_revenue_overlap_low_spend':float(base_overlap)*factor if base_overlap is not None else None,'baseline_trend':trend,'day_of_week_factor':dayfactor,'month_factor':monthfactor,'holiday_lift_factor':holidayfactor,'is_weekend':int(weekday>=5),'is_holiday':int(bool(r['is_holiday']))})
 params={'Email':(.5,80000,2,4.5),'Paid Search':(.1,20000,1.6,3.8),'Social Media':(.5,40000,1.4,3.2),'Display Ads':(.3,15000,1.5,2.8),'TV/Video':(.7,60000,1.8,2.5)};ad=[];sat=[];inc=[];bychannel=collections.defaultdict(list)
 for r in spend_rows:
  if lo<=r['metric_date']<=hi:bychannel[r['channel_name']].append(r)
 for ch,rs in bychannel.items():
  prev=None;stock=0;history=[];decay,half,alpha,coef=params[ch];half/=30
  for r in sorted(rs,key=lambda x:x['metric_date']):
   day=r['metric_date'];stock=float(r['spend_amount'])+(stock*decay**((day-prev).days)if prev is not None else 0);prev=day;history.append((day,stock));ar={'metric_date':day,'channel_name':ch,'raw_spend':r['spend_amount'],'decay_rate':decay,'adstock_spend':stock,'cumulative_adstock_7d':sum(v for d,v in history if 0<=(day-d).days<7),'cumulative_adstock_30d':sum(v for d,v in history if 0<=(day-d).days<30),'row_window_7':sum(v for d,v in history[-7:]),'row_window_30':sum(v for d,v in history[-30:])};ad.append(ar);saturated=stock**alpha/(stock**alpha+half**alpha)*stock if stock>0 else 0;eff=100*saturated/stock if stock else None;ratio=stock/half;sr={'metric_date':day,'channel_name':ch,'adstock_spend':stock,'saturation_halfpoint':half,'saturation_alpha':alpha,'saturated_spend':saturated,'saturation_efficiency_pct':eff,'saturation_status':'Under-invested'if ratio<.7 else'Optimal'if ratio<=1.3 else'Over-saturated'};sat.append(sr);incremental=coef*saturated;inc.append({'metric_date':day,'channel_name':ch,'saturated_spend':saturated,'channel_coefficient':coef,'incremental_revenue':incremental,'raw_roas':incremental/float(r['spend_amount'])if r['spend_amount']else None,'effective_roas':incremental/saturated if saturated else None})
 base_by={r['metric_date']:r for r in baseline};inc_by=collections.defaultdict(dict)
 for r in inc:inc_by[r['metric_date']][r['channel_name']]=r['incremental_revenue']
 decomp=[]
 for day,actual in sorted(sales.items()):
  if not lo<=day<=hi:continue
  row={'metric_date':day,'actual_sales':actual,'baseline_sales':base_by[day]['baseline_revenue']};channels={'Email':'email','Paid Search':'paid_search','Social Media':'social','Display Ads':'display','TV/Video':'tv'}
  for ch,label in channels.items():row[label+'_incremental']=inc_by[day].get(ch,0)
  row['total_incremental']=sum(inc_by[day].values());row['model_predicted_sales']=row['baseline_sales']+row['total_incremental'];row['residual']=float(actual)-row['model_predicted_sales'];row['residual_pct']=100*row['residual']/float(actual)if actual else None;decomp.append(row)
 expected={'spend':spend_rows,'baseline':baseline,'adstock':ad,'saturation':sat,'incremental':inc,'decomposition':decomp};facts={'original_counts':{n:len(rs)for n,rs in raw.items()},'sales_bounds':[min(sales),max(sales)],'spend_bounds':[min(daily_spend),max(daily_spend)],'overlap':[lo,hi],'low_spend_days_all_history':len(low_all),'low_spend_days_overlap':len(low_overlap),'base_demand_all_history':base_all,'base_demand_overlap_only':base_overlap,'canonical_spend_channel_counts':dict(collections.Counter(r['channel_name']for r in spend_rows)),'original_sales_channel_names':sorted({channelnames.get(r['channel_key'],'Unknown')for r in raw['sales']}),'calendar_weekday_examples':[{'date':r['full_date'],'source_day_of_week':r['day_of_week'],'source_day_name':r['day_name'],'python_weekday':r['full_date'].weekday()}for r in calendar[:8]],'zero_daily_spend_rows':sum(v==0 for v in daily_spend.values()),'zero_sales_days':sum(v==0 for v in sales.values())}
 return expected,facts,raw,queries

def derive(raw):
 expected,facts,raw,queries=mmm_expected(raw);dates={r['date_key']:r for r in raw['dates']};names={r['channel_key']:r['channel_name']for r in raw['channels']};groups=collections.defaultdict(list)
 for r in raw['sales']:
  if r['date_key']in dates:groups[(dates[r['date_key']]['full_date'],r['channel_key'],names.get(r['channel_key'],'Unknown'))].append(r)
 expected['sales']=[dict(metric_date=k[0],channel_name=k[2],total_revenue=sum(r['total_amount']or D(0)for r in rs),order_count=len({r['order_id']for r in rs if r['order_id']is not None}),units_sold=sum(r['quantity']or 0 for r in rs))for k,rs in groups.items()]
 expected['calendar']=[dict(metric_date=r['full_date'],day_of_week=r['day_of_week'],day_name=r['day_name'],month=r['month_number'],quarter=r['quarter'],is_weekend=int(bool(r['is_weekend'])),is_holiday=int(bool(r['is_holiday'])))for r in raw['dates']]
 params={'Email':(.5,80000,2,4.5),'Paid Search':(.1,20000,1.6,3.8),'Social Media':(.5,40000,1.4,3.2),'Display Ads':(.3,15000,1.5,2.8),'TV/Video':(.7,60000,1.8,2.5)};expected['mapping']=[dict(channel_name=ch,decay_rate=p[0],saturation_halfpoint=p[1],saturation_alpha=p[2],channel_coefficient=p[3])for ch,p in params.items()];sales=collections.defaultdict(D)
 for r in expected['sales']:sales[r['metric_date']]+=r['total_revenue']
 a={(r['metric_date'],r['channel_name']):r for r in expected['adstock']};sat={(r['metric_date'],r['channel_name']):r for r in expected['saturation']};expected['fact']=[]
 for r in expected['incremental']:
  k=r['metric_date'],r['channel_name'];expected['fact'].append({**r,'raw_spend':a[k]['raw_spend'],'adstock_spend':a[k]['adstock_spend'],'saturation_efficiency_pct':sat[k]['saturation_efficiency_pct'],'contribution_to_total_sales_pct':100*r['incremental_revenue']/float(sales[k[0]])if sales.get(k[0],D(0))else None})
 bychannel=collections.defaultdict(list)
 for r in expected['fact']:bychannel[r['channel_name']].append(r)
 expected['effectiveness']=[]
 for ch,rs in bychannel.items():
  spend=sum(r['raw_spend']for r in rs);inc=sum(r['incremental_revenue']for r in rs);expected['effectiveness'].append(dict(channel_name=ch,total_raw_spend=spend,total_incremental_revenue=inc,avg_saturation_efficiency_pct=sum(r['saturation_efficiency_pct']for r in rs)/len(rs),raw_roas=inc/float(spend)if spend else None))
 weeks=collections.defaultdict(lambda:{'total_sales':D(0),'total_marketing_spend':D(0)})
 for day,v in sales.items():
  if facts['overlap'][0]<=day<=facts['overlap'][1]:weeks[day-datetime.timedelta(days=day.weekday())]['total_sales']+=v
 for r in expected['spend']:
  if facts['overlap'][0]<=r['metric_date']<=facts['overlap'][1]:weeks[r['metric_date']-datetime.timedelta(days=r['metric_date'].weekday())]['total_marketing_spend']+=r['spend_amount']
 expected['weekly']=[dict(time_period=k,**v)for k,v in weeks.items()];expected['actual_sales']=[dict(metric_date=k,actual_sales=v)for k,v in sales.items()if facts['overlap'][0]<=k<=facts['overlap'][1]]
 return expected,facts,raw,queries
