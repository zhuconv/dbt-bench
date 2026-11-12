"""Original-data product derivation, adapted from the retained independent Phase1
probe. The input adapter permits only its two literal raw-source projections;
it does not read native outputs, reference SQL, or gold.
"""
import pathlib,json,hashlib,collections,decimal,datetime
D=decimal.Decimal
SCOLS=['order_line_id','order_id','product_id','product_name','order_date','quantity_ordered','quantity_returned','unit_price','discount_amount','line_total']
ICOLS=['product_id','product_name','total_orders','total_units_sold','total_units_returned','gross_revenue','total_discounts','net_revenue','avg_unit_price','avg_order_quantity']
COLS=['product_id','product_name','total_orders','total_units_sold','total_units_returned','gross_revenue','net_revenue','return_rate','avg_revenue_per_order','revenue_rank','revenue_contribution_pct','cumulative_revenue_pct','abc_class','order_frequency_rank','velocity_class','discount_intensity','margin_indicator','price_position','product_health_score','performance_tier']
def digest(x):return hashlib.sha256(json.dumps(x,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def rnd(x,p='.01'):return D(x).quantize(D(p),rounding=decimal.ROUND_HALF_UP)
def trim(x):return x.strip()if isinstance(x,str)else x
def tilemap(seq):
 n=len(seq);small=n//5;large=n%5;out={};i=0
 for tile in range(1,6):
  for x in seq[i:i+small+(tile<=large)]:out[x]=tile
  i+=small+(tile<=large)
 return out

def derive_captured_rows(c):
 orders=c.execute('SELECT order_id,ordered_at,status FROM main.orders ORDER BY order_id').fetchall();lines=c.execute('SELECT order_line_id,order_id,product_id,product_name,quantity_ordered,quantity_returned,unit_price,discount_amount,line_total,status FROM main.order_lines ORDER BY order_line_id').fetchall()
 om=collections.defaultdict(list)
 for oid,dt,st in orders:om[oid].append((dt,st))
 stage=[];line_statuses=collections.Counter();nulls=collections.Counter();trimmed=collections.Counter()
 for lid,oid,pid,name,qty,ret,price,disc,total,status in lines:
  for dt,st in om[oid]:
   if not(dt and datetime.datetime(2024,1,1)<=dt<datetime.datetime(2025,1,1)and st is not None and st.strip()not in ['CANCELLED','RETURNED','FAILED']):continue
   vals=[lid,oid,pid,name,dt.date(),qty,ret,price,disc,total]
   for col,v in zip(SCOLS,vals):
    if v is None:nulls[col]+=1
    if isinstance(v,str)and v!=v.strip():trimmed[col]+=1
   line_statuses[str(status)]+=1
   stage.append([trim(lid),trim(oid),trim(pid),trim(name),dt.date(),qty,ret if ret is not None else D(0),price,disc if disc is not None else D(0),total])
 by=collections.defaultdict(list)
 for r in stage:by[r[2]].append(r)
 inter=[];name_ties=[];exact_values={}
 for pid,rs in sorted(by.items()):
  names=collections.Counter(r[3]for r in rs);top=max(names.values());choices=sorted(n for n,num in names.items()if num==top)
  if len(choices)>1:name_ties.append({'product_id':pid,'choices':choices,'count':top})
  count=len(set(r[1]for r in rs));sold=sum((r[5]for r in rs),D(0));returned=sum((r[6]for r in rs),D(0));gross=sum((r[9]for r in rs),D(0));discount=sum((r[8]for r in rs),D(0));avgprice=sum((r[7]for r in rs),D(0))/len(rs)
  inter.append([pid,choices[0],count,sold,returned,rnd(gross),rnd(discount),rnd(gross-discount),rnd(avgprice),rnd(sold/count)]);exact_values[pid]={'avg_unit_price':avgprice,'avg_order_quantity':sold/count,'avg_revenue_per_order':rnd(gross-discount)/count}
 im={r[0]:dict(zip(ICOLS,r))for r in inter};n=len(im);total=sum((r['net_revenue']for r in im.values()),D(0));avg=sum((r['avg_unit_price']for r in im.values()),D(0))/n
 netvals=sorted(set(r['net_revenue']for r in im.values()),reverse=True);countvals=sorted(set(r['total_orders']for r in im.values()),reverse=True)
 asc=tilemap(sorted(im,key=lambda x:(im[x]['total_orders'],x)));desc=tilemap(sorted(im,key=lambda x:(-im[x]['total_orders'],x)))
 policies={};profile={'stage_rows':len(stage),'distinct_orders':len(set(r[1]for r in stage)),'products':n,'total_net_revenue':total,'line_statuses':dict(line_statuses),'eligible_nulls':dict(nulls),'eligible_trimmed':dict(trimmed),'name_mode_ties':name_ties,'net_revenue_ties':{str(v):sum(r['net_revenue']==v for r in im.values())for v in netvals if sum(r['net_revenue']==v for r in im.values())>1},'zero_sold':[x for x,r in im.items()if r['total_units_sold']==0],'zero_gross':[x for x,r in im.items()if r['gross_revenue']==0],'rounding_policies':{},'exact_quotients':exact_values}
 for direction in ['ascending_reversed_labels','descending_labels']:
  out=[];running=D(0);rounddiff=[]
  for pid in sorted(im,key=lambda x:(-im[x]['net_revenue'],x)):
   r=im[pid];net=r['net_revenue'];sold=r['total_units_sold'];returned=r['total_units_returned'];gross=r['gross_revenue'];running+=net
   rr=rnd(returned/sold*100)if sold>0 else None;aov=rnd(net/r['total_orders']);share=rnd(net/total*100);cum=rnd(running/total*100);abc='A'if cum<=70 else'B'if cum<=90 else'C';vel=['Stagnant','Slow Mover','Moderate','Good Seller','Fast Mover'][asc[pid]-1]if direction=='ascending_reversed_labels'else['Fast Mover','Good Seller','Moderate','Slow Mover','Stagnant'][desc[pid]-1];discount=rnd(r['total_discounts']/gross*100)if gross>0 else D(0);margin='Healthy'if discount<5 else'Moderate'if discount<15 else'Aggressive'if discount<25 else'Deep Discount';price='Premium'if r['avg_unit_price']>=avg*D('1.5')else'Above Average'if r['avg_unit_price']>=avg*D('1.1')else'Average'if r['avg_unit_price']>=avg*D('.9')else'Below Average'if r['avg_unit_price']>=avg*D('.5')else'Budget';revpoints=D(sum(v['net_revenue']<net for v in im.values()))/(n-1)*35;retpoints=D(25)if rr is None else D(0)if rr>=50 else D(25)-rr/D(2);vp={'Fast Mover':25,'Good Seller':20,'Moderate':15,'Slow Mover':10,'Stagnant':5}[vel];mp={'Healthy':15,'Moderate':10,'Aggressive':5,'Deep Discount':0}[margin];score=max(5,min(100,int(rnd(revpoints,'1')+vp+rnd(retpoints,'1')+mp)));finalround=max(5,min(100,int(rnd(revpoints+vp+retpoints+mp,'1'))));tier='Star Performer'if score>=80 else'Strong Performer'if score>=65 else'Average Performer'if score>=45 else'Underperformer'if score>=25 else'At Risk'
   if score!=finalround:rounddiff.append({'product_id':pid,'component_rounded':score,'sum_rounded':finalround,'exact_revenue_points':revpoints,'exact_return_points':retpoints})
   out.append([pid,r['product_name'],r['total_orders'],rnd(sold),rnd(returned),gross,net,rr,aov,netvals.index(net)+1,share,cum,abc,countvals.index(r['total_orders'])+1,vel,discount,margin,price,score,tier])
  policies[direction]=out;profile['rounding_policies'][direction]=rounddiff
 a={r[0]:r for r in policies['ascending_reversed_labels']};b={r[0]:r for r in policies['descending_labels']};profile['velocity_direction_differences']=[{'product_id':x,'total_orders':im[x]['total_orders'],'ascending_reverse':a[x][14],'descending':b[x][14],'ascending_tile':asc[x],'descending_tile':desc[x]}for x in a if a[x][14]!=b[x][14]]
 raw={name:{'rows':len(rows),'sha256':digest(rows)}for name,rows in [('main.orders',orders),('main.order_lines',lines)]}
 return {'staging_columns':SCOLS,'staging':stage,'intermediate_columns':ICOLS,'intermediate':inter,'mart_columns':COLS,'mart_policy_candidates':policies,'raw':raw,'profile':profile}

def derive(inputs, decode):
 class RawProjection:
  def execute(self, query):
   allowed = {
    'SELECT order_id,ordered_at,status FROM main.orders ORDER BY order_id': ('main.orders', ('order_id','ordered_at','status')),
    'SELECT order_line_id,order_id,product_id,product_name,quantity_ordered,quantity_returned,unit_price,discount_amount,line_total,status FROM main.order_lines ORDER BY order_line_id': ('main.order_lines', ('order_line_id','order_id','product_id','product_name','quantity_ordered','quantity_returned','unit_price','discount_amount','line_total','status'))}
   relation,columns=allowed[query];table=inputs['relations'][relation];names=[v['name'].lower() for v in table['schema']];rows=[dict(zip(names,(decode(v) for v in row)))for row in table['rows']];self.rows=sorted((tuple(r[n]for n in columns)for r in rows),key=lambda r:r[0]);return self
  def fetchall(self):return self.rows
 return derive_captured_rows(RawProjection())
