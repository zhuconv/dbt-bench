import pathlib,json,hashlib,collections,datetime,decimal,itertools
D=decimal.Decimal;ASOF=datetime.datetime(2026,1,22,12)
INPUTS={
'stg_shopping_carts':'cart_id session_id customer_id channel_id status item_count subtotal created_at updated_at converted_at order_id'.split(),
'stg_shopping_cart_items':'cart_id variant_id quantity line_total added_at updated_at'.split(),
'stg_abandoned_carts_stg':'_id _status _loaded_at'.split(),
'stg_web_sessions':'session_id visitor_id customer_id channel_id session_start session_end duration_seconds page_views landing_page exit_page referrer utm_source utm_medium utm_campaign device_type browser os ip_address country is_converted'.split(),
'stg_web_events':'session_id event_name event_type event_timestamp'.split(),
'stg_inventory_levels':'variant_id quantity_available quantity_reserved quantity_on_hand inventory_status'.split(),
'stg_customer__customers':'customer_id customer_type email phone_primary total_lifetime_value total_orders current_tier_id churn_risk_score propensity_to_buy days_since_last_order customer_tenure_days preferred_language preferred_currency customer_segment_snapshot'.split(),
'stg_customer__customer_tiers':'tier_id tier_name tier_level discount_percentage free_shipping'.split(),
'stg_consent_preferences':'customer_id consent_type is_consented consent_date'.split(),
'stg_orders__orders':'order_id customer_id ordered_at status grand_total'.split()}
COLUMNS={
'int_cart_recovery__cart_base':'cart_id session_id customer_id channel_id cart_status cart_item_count_reported cart_subtotal_reported created_at updated_at converted_at order_id abandoned_status abandoned_loaded_at item_line_count distinct_variants total_quantity items_total first_item_added_at last_item_added_at last_item_updated_at cart_merch_value last_activity_at is_converted is_abandoned hours_since_last_activity cart_age_hours'.split(),
'int_cart_recovery__session_signals':INPUTS['stg_web_sessions']+'event_count checkout_event_count payment_error_count add_to_cart_count remove_from_cart_count product_view_count first_event_at last_event_at checkout_depth_score cart_add_net checkout_event_ratio'.split(),
'int_cart_recovery__inventory_risk':'cart_id cart_line_count total_qty line_out_of_stock line_low_stock_status min_quantity_available total_quantity_available inventory_risk_flag inventory_risk_level'.split(),
'int_cart_recovery__customer_context':INPUTS['stg_customer__customers']+'tier_name tier_level discount_percentage free_shipping email_consent sms_consent push_consent personalization_consent last_consent_at'.split(),
'fct_cart_recovery_priority':'cart_id customer_id session_id channel_id cart_status cart_merch_value item_line_count distinct_variants total_quantity last_activity_at hours_since_last_activity cart_age_hours device_type utm_source utm_medium utm_campaign checkout_event_count payment_error_count add_to_cart_count remove_from_cart_count inventory_risk_level tier_name tier_level total_lifetime_value total_orders churn_risk_score propensity_to_buy customer_segment_snapshot recommended_channel priority_score priority_tier recovery_window_hours recovery_window_start recovery_window_end incentive_flag recovered_order_id recovered_at recovered_grand_total is_recovered scored_at'.split()}
def digest(x):return hashlib.sha256(json.dumps(x,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def co(*xs):return next((x for x in xs if x is not None),None)
def upper(x):return (x or'').upper()
def lower(x):return (x or'').lower()
def agg(rows,col,fn):
 vs=[r[col]for r in rows if r.get(col)is not None];return fn(vs)if vs else None
def group(rows,col):
 out=collections.defaultdict(list)
 for r in rows:out[r[col]].append(r)
 return out

def left(mp,key):return mp.get(key,[{}])if key is not None else[{}]
def hours(a,b):return D(str((a-b).total_seconds()))/3600 if b is not None else None

def derive(c):
 raw={};data={}
 for name,cols in INPUTS.items():
  rr=c.execute('SELECT '+','.join('"'+x+'"'for x in cols)+' FROM main.'+name).fetchall();raw[name]={'relation':'main.'+name,'columns':cols,'rows':len(rr),'sha256':digest(sorted(rr,key=lambda r:json.dumps(r,default=str)))};data[name]=[dict(zip(cols,r))for r in rr]
 items=group(data['stg_shopping_cart_items'],'cart_id');ab=group(data['stg_abandoned_carts_stg'],'_id');events=group(data['stg_web_events'],'session_id');inventory=group(data['stg_inventory_levels'],'variant_id');tiers=group(data['stg_customer__customer_tiers'],'tier_id');consents=group(data['stg_consent_preferences'],'customer_id');orders=group(data['stg_orders__orders'],'customer_id');bases=[]
 for cr in data['stg_shopping_carts']:
  rs=items.get(cr['cart_id'],[])if cr['cart_id']is not None else[]
  ir={'item_line_count':len(rs),'distinct_variants':len({r['variant_id']for r in rs if r['variant_id']is not None}),'total_quantity':sum((co(r['quantity'],D(0))for r in rs),D(0)),'items_total':sum((co(r['line_total'],D(0))for r in rs),D(0)),'first_item_added_at':agg(rs,'added_at',min),'last_item_added_at':agg(rs,'added_at',max),'last_item_updated_at':agg(rs,'updated_at',max)}if rs else{k:None for k in COLUMNS['int_cart_recovery__cart_base'][13:20]}
  for ar in left(ab,cr['cart_id']):
   b={k:cr[k]for k in ['cart_id','session_id','customer_id','channel_id','created_at','updated_at','converted_at','order_id']};b.update(cart_status=cr['status'],cart_item_count_reported=cr['item_count'],cart_subtotal_reported=cr['subtotal'],abandoned_status=ar.get('_status'),abandoned_loaded_at=ar.get('_loaded_at'),**ir);b['cart_merch_value']=co(b['items_total'],cr['subtotal'],D(0));times=[co(cr['updated_at'],cr['created_at']),co(ir['last_item_updated_at'],ir['last_item_added_at'],cr['created_at']),co(cr['converted_at'],cr['created_at'])];b['last_activity_at']=max(x for x in times if x is not None)if any(x is not None for x in times)else None;b['is_converted']=cr['order_id']is not None or cr['converted_at']is not None or upper(cr['status'])=='CONVERTED';b['is_abandoned']=upper(cr['status'])=='ABANDONED'or upper(ar.get('_status'))=='ABANDONED'or(not b['is_converted']and upper(cr['status'])in['CANCELLED','EXPIRED']);b['hours_since_last_activity']=hours(ASOF,b['last_activity_at']);b['cart_age_hours']=hours(ASOF,b['created_at']);bases.append(b)
 sessions=[]
 for sr in data['stg_web_sessions']:
  es=events.get(sr['session_id'],[])if sr['session_id']is not None else[];r=dict(sr)
  if es:r.update(event_count=len(es),checkout_event_count=sum('checkout'in lower(e['event_name'])or'checkout'in lower(e['event_type'])for e in es),payment_error_count=sum('payment'in lower(e['event_name'])and lower(e['event_type'])=='error'for e in es),add_to_cart_count=sum('add'in lower(e['event_name'])and'cart'in lower(e['event_name'])for e in es),remove_from_cart_count=sum('remove'in lower(e['event_name'])and'cart'in lower(e['event_name'])for e in es),product_view_count=sum('view'in lower(e['event_name'])and'product'in lower(e['event_name'])for e in es),first_event_at=agg(es,'event_timestamp',min),last_event_at=agg(es,'event_timestamp',max))
  else:r.update({k:None for k in COLUMNS['int_cart_recovery__session_signals'][20:28]})
  r['checkout_depth_score']=2 if co(r['payment_error_count'],0)>0 else 1 if co(r['checkout_event_count'],0)>0 else 0;r['cart_add_net']=co(r['add_to_cart_count'],0)-co(r['remove_from_cart_count'],0);r['checkout_event_ratio']=D(co(r['checkout_event_count'],0))/r['event_count']if r['event_count']else None;sessions.append(r)
 risks=[]
 for cid,rs in items.items():
  joined=[(r,v)for r in rs for v in left(inventory,r['variant_id'])];out=sum(co(v.get('quantity_available'),0)<co(r['quantity'],0)for r,v in joined);low=sum(upper(v.get('inventory_status'))in['LOW_STOCK','OUT_OF_STOCK']for r,v in joined);av=[v['quantity_available']for r,v in joined if v.get('quantity_available')is not None];risks.append({'cart_id':cid,'cart_line_count':len(joined),'total_qty':sum((co(r['quantity'],D(0))for r,v in joined),D(0)),'line_out_of_stock':out,'line_low_stock_status':low,'min_quantity_available':min(av)if av else None,'total_quantity_available':sum((co(v.get('quantity_available'),D(0))for r,v in joined),D(0)),'inventory_risk_flag':out>0 or low>0,'inventory_risk_level':'OUT_OF_STOCK'if out>0 else'LOW_STOCK'if low>0 else'OK'})
 contexts=[];types={'email':['MARKETING_EMAIL','EMAIL'],'sms':['MARKETING_SMS','SMS'],'push':['MARKETING_PUSH','PUSH','PUSH_NOTIFICATIONS'],'personalization':['PERSONALIZATION','ANALYTICS']}
 for cr in data['stg_customer__customers']:
  prefs=consents.get(cr['customer_id'],[])if cr['customer_id']is not None else[]
  for tr in left(tiers,cr['current_tier_id']):
   r=dict(cr);r.update({x:tr.get(x)for x in['tier_name','tier_level','discount_percentage','free_shipping']});r.update({name+'_consent':int(any(upper(z['consent_type'])in options and z['is_consented']is True for z in prefs))for name,options in types.items()});r['last_consent_at']=agg(prefs,'consent_date',max);contexts.append(r)
 sm=group(sessions,'session_id');im=group(risks,'cart_id');cm=group(contexts,'customer_id');matches={};match_candidates={}
 for b in bases:
  candidates=[o for o in orders.get(b['customer_id'],[])if b['customer_id']is not None and b['last_activity_at']is not None and o['ordered_at']is not None and b['last_activity_at']<=o['ordered_at']<=b['last_activity_at']+datetime.timedelta(days=7)and upper(o['status'])not in['CANCELLED','VOID','TEST']]
  if candidates:match_candidates.setdefault(b['cart_id'],[]).extend(candidates)
 for cid,rs in match_candidates.items():matches[cid]={'recovered_at':agg(rs,'ordered_at',min),'recovered_order_id':agg(rs,'order_id',min),'recovered_grand_total':agg(rs,'grand_total',min)}
 final=[]
 for b in bases:
  if not b['is_abandoned']:continue
  for s,i,u in itertools.product(left(sm,b['session_id']),left(im,b['cart_id']),left(cm,b['customer_id'])):
   r={col:b.get(col)for col in COLUMNS['fct_cart_recovery_priority']};r.update({col:s.get(col)for col in['device_type','utm_source','utm_medium','utm_campaign','checkout_event_count','payment_error_count','add_to_cart_count','remove_from_cart_count']});r['inventory_risk_level']=i.get('inventory_risk_level');r.update({col:u.get(col)for col in['tier_name','tier_level','total_lifetime_value','total_orders','churn_risk_score','propensity_to_buy','customer_segment_snapshot']});value=b['cart_merch_value'];tier=co(u.get('tier_level'),0);pay=co(s.get('payment_error_count'),0);remove=co(s.get('remove_from_cart_count'),0);risk=i.get('inventory_risk_flag');score=(30 if value>=500 else 22 if value>=200 else 15 if value>=100 else 8)+(20 if s.get('checkout_depth_score')==2 else 12 if s.get('checkout_depth_score')==1 else 5)+(15 if tier>=4 else 10 if tier>=3 else 6 if tier>=2 else 3)+(-5 if pay>0 else-2 if remove>0 else 0)+(-5 if risk else 0);window=2 if score>=55 else 24 if score>=40 else 72;r.update(priority_score=score,priority_tier='P0'if score>=55 else'P1'if score>=40 else'P2',recommended_channel='EMAIL'if u.get('email_consent')==1 else'SMS'if u.get('sms_consent')==1 else'PUSH'if u.get('push_consent')==1 else'SUPPRESS',recovery_window_hours=window,recovery_window_start=b['last_activity_at'],recovery_window_end=b['last_activity_at']+datetime.timedelta(hours=window)if b['last_activity_at']else None,incentive_flag=bool((score>=55 and(pay>0 or risk))or(score>=40 and pay>0)),scored_at=ASOF);r.update(matches.get(b['cart_id'],{'recovered_at':None,'recovered_order_id':None,'recovered_grand_total':None}));r['is_recovered']=r['recovered_order_id']is not None or b['is_converted'];final.append(r)
 outputs={'int_cart_recovery__cart_base':bases,'int_cart_recovery__session_signals':sessions,'int_cart_recovery__inventory_risk':risks,'int_cart_recovery__customer_context':contexts,'fct_cart_recovery_priority':final};profile={'counts':{k:len(v)for k,v in outputs.items()},'abandoned_and_converted':sum(b['is_abandoned']and b['is_converted']for b in bases),'sessions_without_events':sum(s['event_count']is None for s in sessions),'actual_recovery_matches':len(matches),'consent_input_types':sorted({type(z['is_consented']).__name__ for z in data['stg_consent_preferences']}),'final_null_inventory':sum(r['inventory_risk_level']is None for r in final),'priority_tiers':dict(collections.Counter(r['priority_tier']for r in final)),'channels':dict(collections.Counter(r['recommended_channel']for r in final))}
 return {'columns':COLUMNS,'expected':{k:[[r.get(col)for col in COLUMNS[k]]for r in rs]for k,rs in outputs.items()},'raw':raw,'profile':profile,'scope':'Independent Python multimap joins and aggregates from ten explicitly allowed staging inputs. No native gold/test defines expectation. Recovery uses independently minimum ordered_at/order_id/grand_total as explicitly allowed examples, not an invented same-row rule. Fallback last-activity policy is profiled against actual input.'}


def derive_capture(capture,decode):
 """Adapt actual bounded source projections to the independent Python joins.
 No query execution or reference output is needed; source order is preserved.
 """
 class Projection:
  def execute(self,sql):
   relation=sql.rsplit(' FROM ',1)[1];name=relation.split('.')[-1];table=capture['relations'][relation];columns=[v['name'].lower()for v in table['schema']];assert columns==INPUTS[name];self.rows=[tuple(map(decode,row))for row in table['rows']];return self
  def fetchall(self):return self.rows
 return derive(Projection())
