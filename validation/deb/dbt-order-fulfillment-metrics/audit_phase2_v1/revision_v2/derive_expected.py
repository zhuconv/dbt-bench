import pathlib,json,hashlib,collections,datetime,decimal
D=decimal.Decimal
COLS=['order_type','total_orders','orders_shipped','orders_delivered','fulfillment_rate','delivery_rate','avg_processing_days','min_processing_days','max_processing_days','avg_delivery_days','min_delivery_days','max_delivery_days','total_revenue','avg_order_value','volume_rank','revenue_rank','volume_percentile','revenue_share_pct','efficiency_score','performance_tier','fulfillment_grade']
def digest(x):return hashlib.sha256(json.dumps(x,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def rnd(x,n=2):return D(str(x)).quantize(D(10)**(-n),rounding=decimal.ROUND_HALF_UP)
def clean(x):return str(x).strip().upper()if x is not None else None
def derive(c,include_cancelled=False):
 raw=c.execute('SELECT order_id,order_type,status,grand_total,ordered_at,shipped_at,delivered_at FROM ORDERS.ORDERS').fetchall();by=collections.defaultdict(list)
 for r in raw:
  if clean(r[2])in(['COMPLETED','DELIVERED','SHIPPED','CANCELLED']if include_cancelled else['COMPLETED','DELIVERED','SHIPPED']):by[clean(r[1])].append(r)
 expected=[];precision={};profile={'input_rows':len(raw),'eligible_rows':sum(len(rs)for rs in by.values()),'eligible_null_revenue':sum(r[3]is None for rs in by.values()for r in rs),'eligible_null_ordered_at':sum(r[4]is None for rs in by.values()for r in rs),'eligible_duplicate_order_ids':sum(len(rs)for rs in by.values())-len({r[0]for rs in by.values()for r in rs}),'no_shipping_types':[],'no_delivery_types':[],'threshold_observations':[]}
 for typ,rs in by.items():
  count=len(rs);shipped=sum(r[5]is not None for r in rs);delivered=sum(r[6]is not None for r in rs);money=sum((D(str(r[3]))if r[3]is not None else D(0)for r in rs),D(0));processing=[(r[5].date()-r[4].date()).days for r in rs if r[4]is not None and r[5]is not None];delivery=[(r[6].date()-r[4].date()).days for r in rs if r[4]is not None and r[6]is not None];avg_p=D(sum(processing))/len(processing)if processing else D(0);avg_d=D(sum(delivery))/len(delivery)if delivery else D(0);fulfill=100*D(shipped)/count;deliveryrate=100*D(delivered)/count;expected.append([typ,count,shipped,delivered,rnd(fulfill,4),rnd(deliveryrate,4),rnd(avg_p),min(processing)if processing else 0,max(processing)if processing else 0,rnd(avg_d),min(delivery)if delivery else 0,max(delivery)if delivery else 0,rnd(money),rnd(money/count),None,None,None,None,None,None,None]);precision[typ]={'4':fulfill,'5':deliveryrate,'6':avg_p,'9':avg_d,'12':money,'13':money/count}
  if not processing:profile['no_shipping_types'].append(typ)
  if not delivery:profile['no_delivery_types'].append(typ)
 total=sum((r[12]for r in expected),D(0))
 for r in expected:
  r[14]=1+sum(e[1]>r[1]for e in expected);r[15]=1+sum(e[12]>r[12]for e in expected);pct=D(sum(e[1]<r[1]for e in expected))/max(len(expected)-1,1);share=r[12]*100/total;r[16]=rnd(pct,4);r[17]=rnd(share,4);eff=r[4]/100*D('.4')+r[5]/100*D('.4')+D('.2')/(r[6]+1);r[18]=rnd(eff,4);r[19]='High Performance'if r[16]>=D('.66')else'Medium Performance'if r[16]>=D('.33')else'Low Performance';r[20]='Excellent'if r[4]>=90 else'Good'if r[4]>=70 else'Average'if r[4]>=50 else'Poor';precision[r[0]].update({'16':pct,'17':share,'18':eff});profile['threshold_observations'].append({'order_type':r[0],'percentile':r[16],'fulfillment_rate':r[4]})
 profile['exact_before_final_rounding']=precision;return expected,{'relation':'ORDERS.ORDERS','columns':['order_id','order_type','status','grand_total','ordered_at','shipped_at','delivered_at'],'rows':len(raw),'sha256':digest(raw)},profile
if __name__=='__main__':
 with duckdb.connect('/app/database/retail.duckdb',read_only=True)as c:e,r,p=derive(c)
 pathlib.Path('/review/fulfillment-metrics-original-derived.json').write_text(json.dumps({'columns':COLS,'expected':e,'raw':r,'profile':p,'scope':'IndependentPythonstatus/typecleaning, actualdateboundarydifferences, pertypecounts/SUM/AVG/extrema, rankandascendingpercentilerules, andexplicitformulasoverroundedoutputrates/times. Noverifierexpectedused.'},default=str,indent=2)+'\n');print(e,r,{k:v for k,v in p.items()if k!='exact_before_final_rounding'})
