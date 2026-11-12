"""Bounded independent current-input arithmetic, without reference/gold access."""
from collections import Counter,defaultdict
from decimal import Decimal as D,ROUND_HALF_UP
COLS={'int_finance__exchange_rates_daily':['rate_date','from_currency','to_currency','rate_to_usd'],'int_procurement__purchases_enriched':['purchase_id','po_id','sku','quantity','unit_cost','currency','purchase_date'],'int_orders__sales_enriched':['sale_id','order_id','sku','quantity_sold','sale_date'],'purchase_costs_usd':['purchase_id','sku','quantity','original_currency','original_unit_cost','purchase_date','exchange_rate','unit_cost_usd','total_cost_usd'],'sale_cogs':['sale_id','order_id','sku','sale_date','quantity_sold','cogs_usd','avg_unit_cost','batches_consumed','inventory_shortfall','fallback_cost_usd','total_estimated_cogs','costing_method'],'inventory_turnover_metrics':['sku','total_purchased_qty','total_purchased_cost_usd','total_sold_qty','total_cogs_usd','remaining_inventory_qty','remaining_inventory_cost_usd','weighted_avg_purchase_cost','weighted_avg_sale_cost','inventory_turnover_ratio','gross_margin_pct']}
KEYS={'int_finance__exchange_rates_daily':['rate_date','from_currency','to_currency'],'int_procurement__purchases_enriched':['purchase_id'],'int_orders__sales_enriched':['sale_id'],'purchase_costs_usd':['purchase_id'],'sale_cogs':['sale_id'],'inventory_turnover_metrics':['sku']}
def decimal(x):return x if isinstance(x,D)else D(str(x))
def q(x,n):return decimal(x).quantize(D(1).scaleb(-n),rounding=ROUND_HALF_UP)
def day(x):return x.date()if hasattr(x,'hour')else x

def derive(raw):
 ph={r['po_id']:r for r in raw['purchase_headers']};sh={r['order_id']:r for r in raw['sales_headers']};p=[];s=[]
 for l in raw['purchase_lines']:
  h=ph.get(l['po_id']);qty=l['quantity_received']
  if h and l['sku']is not None and qty is not None and qty>0 and l['unit_price']is not None:
   if decimal(qty)!=decimal(qty).to_integral():raise ValueError('Fractional purchase quantity policy unestablished')
   p.append(dict(purchase_id=l['po_line_id'],po_id=l['po_id'],sku=l['sku'],quantity=int(qty),unit_cost=l['unit_price'],currency=h['currency_code'],purchase_date=day(h['ordered_at'])))
 for l in raw['sales_lines']:
  h=sh.get(l['order_id']);qty=l['quantity_ordered']
  if h and l['sku']is not None and qty is not None and qty>0:
   if decimal(qty)!=decimal(qty).to_integral():raise ValueError('Fractional sales quantity policy unestablished')
   s.append(dict(sale_id=l['order_line_id'],order_id=l['order_id'],sku=l['sku'],quantity_sold=int(qty),sale_date=day(h['ordered_at'])))
 overlap=sorted({r['sku']for r in p}&{r['sku']for r in s});currencies=Counter(str(r['currency'])for r in p)
 if overlap or set(currencies)-{'USD'}:raise ValueError('Bounded zero-overlap/all-USD checker cannot generalize this input')
 pc=[dict(purchase_id=r['purchase_id'],sku=r['sku'],quantity=r['quantity'],original_currency=r['currency'],original_unit_cost=q(r['unit_cost'],4),purchase_date=r['purchase_date'],exchange_rate=D('1.000000'),unit_cost_usd=q(r['unit_cost'],4),total_cost_usd=q(q(r['unit_cost'],4)*r['quantity'],2))for r in p]
 sc=[dict(**r,cogs_usd=D('0.00'),avg_unit_cost=D('0.0000'),batches_consumed=0,inventory_shortfall=r['quantity_sold'],fallback_cost_usd=D('0.00'),total_estimated_cogs=D('0.00'),costing_method='LIFO_NONE')for r in s]
 pg=defaultdict(list);sg=defaultdict(list)
 for r in pc:pg[r['sku']].append(r)
 for r in sc:sg[r['sku']].append(r)
 inv=[]
 for sku in sorted(pg.keys()|sg.keys()):
  pq=sum(r['quantity']for r in pg[sku]);cost=sum((r['total_cost_usd']for r in pg[sku]),D(0));sq=sum(r['quantity_sold']for r in sg[sku]);inv.append(dict(sku=sku,total_purchased_qty=pq,total_purchased_cost_usd=q(cost,2),total_sold_qty=sq,total_cogs_usd=D('0.00'),remaining_inventory_qty=pq-sq,remaining_inventory_cost_usd=q(cost,2),weighted_avg_purchase_cost=q(cost/pq,4)if pq else D('0.0000'),weighted_avg_sale_cost=D('0.0000'),inventory_turnover_ratio=D('0.0000')if cost>0 else None,gross_margin_pct=None))
 rates=[dict(rate_date=day(r['effective_date']),from_currency=r['from_currency'],to_currency=r['to_currency'],rate_to_usd=r['exchange_rate'])for r in raw['rates']if r['to_currency']=='USD']
 rows={'int_finance__exchange_rates_daily':rates,'int_procurement__purchases_enriched':p,'int_orders__sales_enriched':s,'purchase_costs_usd':pc,'sale_cogs':sc,'inventory_turnover_metrics':inv}
 alternate={name:[dict(r)for r in rs]for name,rs in rows.items()}
 for r in alternate['sale_cogs']:r['fallback_cost_usd']=None;r['total_estimated_cogs']=None
 for r in alternate['inventory_turnover_metrics']:
  if not r['total_purchased_qty']:r['weighted_avg_purchase_cost']=None
  if not r['total_sold_qty']:r['weighted_avg_sale_cost']=None
 return dict(rows=rows,alternate_undefined_pool_null=alternate,columns=COLS,keys=KEYS,profile={'input_rows':{k:len(v)for k,v in raw.items()},'purchase_rows':len(p),'sales_rows':len(s),'purchase_currency_counts':dict(currencies),'overlap_skus':overlap,'sales_without_any_purchase_pool':len(s),'purchase_only_skus':sum(bool(v)for v in pg.values()),'sales_only_skus':sum(bool(v)for v in sg.values()),'repeated_purchase_skus':sum(len(v)>1 for v in pg.values()),'undefined_denominator_policy':'Actual zero-vs-NULL arithmetic alternative executed. No purchase pool supplies a weighted average for any sale; instructions do not prescribe empty-pool fallback or purchase-only weighted-sale-average.'},scope='All-USD/no-sale-purchase-SKU-overlap original-input specialization. Nonzero allocation, FX and chronological branches remain untested by this positive input. No native/gold output used.')
