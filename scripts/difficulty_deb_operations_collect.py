"""Read-only current-submission source reconstruction capture for nine DEB tasks.

Frozen raw input projections; no gold/author SQL. Missing current model outputs
are recorded as explicit per-model errors, not a failed raw-input capture.
"""
import argparse,datetime,decimal,hashlib,json,os
from pathlib import Path

RAW = {'dbt-customer-risk-scoring': {'customers': 'SELECT customer_id,first_name,last_name FROM main.customers',
                               'orders': 'SELECT '
                                         'order_id,customer_id,status,payment_status,chargeback_flag,ordered_at '
                                         'FROM main.orders'},
 'dbt-daily-order-summary': {'orders': 'SELECT ORDER_ID,ORDERED_AT,GRAND_TOTAL,STATUS FROM ORDERS.ORDERS'},
 'dbt-fraud-detection-model': {'addresses': 'SELECT address_id,state_province FROM main.addresses',
                               'lines': 'SELECT order_line_id,order_id,quantity_ordered FROM '
                                        'main.order_lines',
                               'orders': 'SELECT '
                                         'order_id,customer_id,ordered_at,grand_total,billing_address_id,shipping_address_id,chargeback_flag,is_first_order '
                                         'FROM main.orders'},
 'dbt-fulfillment-sla': {'carriers': 'SELECT carrier_id,carrier_code,carrier_name,carrier_type FROM '
                                     'main.stg_reference__carriers',
                         'methods': 'SELECT '
                                    'shipping_method_id,shipping_method_code,shipping_method_name,carrier_id,estimated_days_min,estimated_days_max,is_express '
                                    'FROM main.stg_wms__ship_methods',
                         'orders': 'SELECT '
                                   'order_id,customer_id,ordered_at,shipped_at,delivered_at,grand_total,status,test_order_flag,is_first_order,warehouse_id '
                                   'FROM main.stg_orders__orders',
                         'shipments': 'SELECT '
                                      'shipment_id,order_id,warehouse_id,carrier_id,shipping_method_id,shipping_cost,weight '
                                      'FROM main.stg_orders__shipments',
                         'warehouses': 'SELECT '
                                       'warehouse_id,warehouse_code,warehouse_name,warehouse_type,state_province,country_code '
                                       'FROM main.stg_wms__warehouses'},
 'dbt-inventory-analysis': {'input_0': 'SELECT '
                                       '"inventory_id","variant_id","warehouse_id","quantity_on_hand","quantity_available","quantity_reserved","unit_cost" '
                                       'FROM INVENTORY.INVENTORY_LEVELS',
                            'input_1': 'SELECT '
                                       '"warehouse_id","warehouse_name","warehouse_type","city","state_province" '
                                       'FROM INVENTORY.WAREHOUSES'},
 'dbt-product-category-analytics': {'input_0': 'SELECT '
                                               '"ORDER_ID","ORDER_NUMBER","CUSTOMER_ID","ORDER_TYPE","ORDER_SOURCE","CHANNEL_ID","CURRENCY_CODE","EXCHANGE_RATE","BILLING_ADDRESS_ID","SHIPPING_ADDRESS_ID","SUBTOTAL","DISCOUNT_TOTAL","SHIPPING_TOTAL","TAX_TOTAL","GRAND_TOTAL","STATUS","PAYMENT_STATUS","FULFILLMENT_STATUS","ORDERED_AT","SHIPPED_AT","DELIVERED_AT","CANCELLED_AT","IP_ADDRESS","USER_AGENT","NOTES","CREATED_AT","UPDATED_AT","LEGACY_ORDER_ID","OLD_ORDER_NUMBER","AS400_ORDER_REF","MIGRATION_SOURCE","MIGRATION_DATE","PRE_MIGRATION_STATUS","CONVERTED_ORDER_FLAG","SOURCE_SYSTEM_CODE","ORIGINAL_ORDER_DATE","LEGACY_CHANNEL_CODE","SALESFORCE_OPPORTUNITY_ID","NETSUITE_TRANSACTION_ID","SHOPIFY_ORDER_ID","STRIPE_PAYMENT_INTENT_ID","SHIPSTATION_ORDER_ID","RETURNLY_RMA_ID","LOOP_RETURN_ID","AFFIRM_CHECKOUT_ID","COST_OF_GOODS","GROSS_MARGIN","GROSS_MARGIN_PCT","COMMISSION_AMOUNT","AFFILIATE_FEE","PAYMENT_PROCESSING_FEE","NET_REVENUE","TAX_EXEMPT_AMOUNT","GIFT_CARD_AMOUNT","STORE_CREDIT_AMOUNT","LOYALTY_POINTS_USED","LOYALTY_POINTS_EARNED","WAREHOUSE_ID","FULFILLMENT_CENTER","CARRIER_CODE","SERVICE_LEVEL","ESTIMATED_SHIP_DATE","ACTUAL_SHIP_DATE","ESTIMATED_DELIVERY_DATE","ACTUAL_DELIVERY_DATE","DELIVERY_SIGNATURE","DELIVERY_INSTRUCTIONS","FRAUD_SCORE","FRAUD_CHECK_STATUS","FRAUD_REVIEW_DATE","FRAUD_REVIEWER_ID","AVS_RESPONSE","CVV_RESPONSE","IP_FRAUD_SCORE","CHARGEBACK_FLAG","UTM_SOURCE","UTM_MEDIUM","UTM_CAMPAIGN","UTM_CONTENT","UTM_TERM","REFERRER_URL","LANDING_PAGE","ATTRIBUTION_CHANNEL","FIRST_TOUCH_CAMPAIGN","LAST_TOUCH_CAMPAIGN","TEMP_HOLD_FLAG","ANALYST_REVIEW_FLAG","ANALYST_NOTES","EXCLUDE_FROM_REPORTING","TEST_ORDER_FLAG","SAMPLE_ORDER_FLAG","INTERNAL_ORDER_FLAG","REPROCESS_FLAG","PCI_COMPLIANT","AUDIT_TRAIL_ID","APPROVAL_STATUS","APPROVED_BY","APPROVAL_DATE","EXPORT_CONTROL_STATUS","TAX_REPORTING_STATUS","INVOICE_GENERATED_FLAG","CUSTOMER_SEGMENT_AT_ORDER","CUSTOMER_TIER_AT_ORDER","CUSTOMER_LTV_AT_ORDER","CUSTOMER_ORDER_COUNT_AT_ORDER","CUSTOMER_TENURE_DAYS_AT_ORDER","IS_FIRST_ORDER","LEGACY_ORDER_TYPE","CANCELLATION_REASON" '
                                               'FROM main.orders',
                                    'input_1': 'SELECT '
                                               '"ORDER_LINE_ID","ORDER_ID","LINE_NUMBER","VARIANT_ID","PRODUCT_ID","SKU","PRODUCT_NAME","VARIANT_NAME","QUANTITY_ORDERED","QUANTITY_SHIPPED","QUANTITY_RETURNED","UNIT_PRICE","DISCOUNT_AMOUNT","TAX_AMOUNT","LINE_TOTAL","STATUS","CREATED_AT","UPDATED_AT" '
                                               'FROM main.order_lines',
                                    'input_2': 'SELECT '
                                               '"ORDER_LINE_ID","ORDER_ID","LINE_NUMBER","VARIANT_ID","PRODUCT_ID","SKU","PRODUCT_NAME","VARIANT_NAME","QUANTITY_ORDERED","QUANTITY_SHIPPED","QUANTITY_RETURNED","UNIT_PRICE","DISCOUNT_AMOUNT","TAX_AMOUNT","LINE_TOTAL","STATUS","CREATED_AT","UPDATED_AT" '
                                               'FROM ORDERS.ORDER_LINES',
                                    'input_3': 'SELECT '
                                               '"PRODUCT_ID","PRODUCT_CODE","PRODUCT_NAME","PRODUCT_DESCRIPTION","SHORT_DESCRIPTION","BRAND_ID","PRIMARY_CATEGORY_ID","PRODUCT_TYPE","BASE_UOM","WEIGHT","WEIGHT_UOM","LENGTH","WIDTH","HEIGHT","DIMENSION_UOM","IS_SERIALIZED","IS_LOT_TRACKED","IS_PERISHABLE","SHELF_LIFE_DAYS","COUNTRY_OF_ORIGIN","HS_CODE","TAX_CLASS","WARRANTY_MONTHS","META_TITLE","META_DESCRIPTION","META_KEYWORDS","CANONICAL_URL","LIFECYCLE_STATUS","LAUNCHED_AT","DISCONTINUED_AT","IS_ACTIVE","CREATED_AT","UPDATED_AT","CREATED_BY","UPDATED_BY","LEGACY_PRODUCT_ID","OLD_SKU","AS400_ITEM_CODE","SAP_MATERIAL_NUMBER","ORACLE_ITEM_ID","MIGRATION_SOURCE","MIGRATION_DATE","PRE_MIGRATION_STATUS","LEGACY_CATEGORY_CODE","LEGACY_BRAND_CODE","ORIGINAL_CREATE_DATE","SOURCE_SYSTEM_CODE","SHOPIFY_PRODUCT_ID","SHOPIFY_VARIANT_ID","AMAZON_ASIN","WALMART_ITEM_ID","GOOGLE_PRODUCT_ID","FACEBOOK_PRODUCT_ID","PINTEREST_PIN_ID","SALESFORCE_PRODUCT_ID","NETSUITE_ITEM_ID","PIM_PRODUCT_ID","COST_PRICE","WHOLESALE_PRICE","MAP_PRICE","MSRP","COMPARE_AT_PRICE","MEMBER_PRICE","CLEARANCE_PRICE","LAST_PRICE_CHANGE_DATE","PRICE_CHANGE_REASON","MARGIN_PCT","MARKUP_PCT","PRICE_TIER","PROMOTIONAL_PRICE","PROMO_START_DATE","PROMO_END_DATE","REORDER_POINT","REORDER_QUANTITY","SAFETY_STOCK","LEAD_TIME_DAYS","ABC_CLASSIFICATION","VELOCITY_CODE","LAST_RECEIVED_DATE","LAST_SOLD_DATE","DAYS_OF_SUPPLY","STOCKOUT_COUNT_YTD","BACKORDER_ALLOWED","DROPSHIP_ELIGIBLE","WEIGHT_LBS","LENGTH_IN","WIDTH_IN","HEIGHT_IN","CUBIC_FEET","UPC_CODE","EAN_CODE","GTIN","MPN","CONDITION_CODE","AGE_RESTRICTED","HAZMAT_FLAG","FDA_REGISTERED","PROP65_WARNING","CPSIA_COMPLIANT","ENERGY_STAR","ORGANIC_CERTIFIED","FAIR_TRADE","COMPLIANCE_STATUS","LAST_COMPLIANCE_CHECK","DEMAND_FORECAST_MONTHLY","FORECAST_ACCURACY","TRENDING_SCORE","SEASONAL_INDEX","CANNIBALIZATION_RISK","CROSS_SELL_AFFINITY","SEARCH_RANK","RECOMMENDATION_SCORE","RETURN_RATE","REVIEW_SENTIMENT_SCORE","TEMP_DISCONTINUE_FLAG","ANALYST_NOTES","REVIEW_FLAG","EXCLUDE_FROM_FEED","TEST_PRODUCT_FLAG","SAMPLE_PRODUCT_FLAG","PHOTO_SHOOT_NEEDED","CONTENT_REVIEW_STATUS","OLD_CATEGORY_ID","DEPRECATED_BRAND_ID","V1_PRODUCT_TYPE","LEGACY_TAX_CODE","OLD_VENDOR_CODE","PREVIOUS_UPC","ARCHIVE_FLAG","SUPERSEDED_BY" '
                                               'FROM PRODUCT.PRODUCTS',
                                    'input_4': 'SELECT '
                                               '"CATEGORY_ID","CATEGORY_CODE","CATEGORY_NAME","CATEGORY_DESCRIPTION","PARENT_CATEGORY_ID","CATEGORY_LEVEL","CATEGORY_PATH","CATEGORY_PATH_IDS","SORT_ORDER","IMAGE_URL","ICON_NAME","META_TITLE","META_DESCRIPTION","META_KEYWORDS","IS_FEATURED","IS_ACTIVE","CREATED_AT","UPDATED_AT" '
                                               'FROM PRODUCT.PRODUCT_CATEGORIES'},
 'dbt-retail-task-01-channel-revenue': {'channels': 'SELECT '
                                                    'channel_key,channel_id,channel_code,channel_name,channel_type,is_active '
                                                    'FROM main.dim_dim_channel',
                                        'dates': 'SELECT date_key,full_date,month_number,year FROM '
                                                 'main.stg_analytics__dim_date',
                                        'fact_keys': 'SELECT sales_id,date_key,channel_key,order_id FROM '
                                                     'main.dim_fact_sales',
                                        'fact_values': 'SELECT '
                                                       'sale_key,total_amount,profit_amount,discount_amount '
                                                       'FROM main.stg_fact_sales'},
 'dbt-supplier-scorecard': {'purchase_order_receipt_lines': 'SELECT '
                                                            'receipt_line_id,receipt_id,po_line_id,quantity_received,quantity_accepted,quantity_rejected,reject_reason '
                                                            'FROM PROCUREMENT.PURCHASE_ORDER_RECEIPT_LINES',
                            'purchase_order_receipts': 'SELECT '
                                                       'receipt_id,receipt_number,po_id,received_at,status '
                                                       'FROM PROCUREMENT.PURCHASE_ORDER_RECEIPTS',
                            'purchase_orders': 'SELECT '
                                               'po_id,po_number,supplier_id,warehouse_id,status,total_amount,expected_date,ordered_at '
                                               'FROM PROCUREMENT.PURCHASE_ORDERS',
                            'supplier_invoices': 'SELECT '
                                                 'invoice_id,invoice_number,supplier_id,po_id,invoice_date,total_amount,status '
                                                 'FROM PROCUREMENT.SUPPLIER_INVOICES',
                            'suppliers': 'SELECT '
                                         'supplier_id,supplier_code,supplier_name,supplier_type,lead_time_days,status '
                                         'FROM PROCUREMENT.SUPPLIERS'},
 'dbt-three-way-matching': {'invoice_lines': 'SELECT '
                                             'invoice_line_id,invoice_id,po_line_id,quantity,unit_price,line_total '
                                             'FROM main.stg_procurement__supplier_invoice_lines',
                            'invoices': 'SELECT invoice_id,invoice_date FROM '
                                        'main.stg_procurement__supplier_invoices',
                            'po': 'SELECT po_id,po_number,supplier_id,ordered_at FROM '
                                  'main.stg_procurement__purchase_orders',
                            'pol': 'SELECT '
                                   'po_line_id,po_id,variant_id,sku,quantity_ordered,unit_price,line_total '
                                   'FROM main.stg_procurement__purchase_order_lines',
                            'receipt_lines': 'SELECT '
                                             'receipt_line_id,receipt_id,po_line_id,quantity_received,quantity_accepted,quantity_rejected '
                                             'FROM main.stg_procurement__purchase_order_receipt_lines',
                            'receipts': 'SELECT receipt_id,received_at FROM '
                                        'main.stg_procurement__purchase_order_receipts',
                            'suppliers': 'SELECT supplier_id,supplier_name,rating FROM '
                                         'main.stg_procurement__suppliers'}}
MODELS = {'dbt-customer-risk-scoring': ['stg_orders_risk', 'stg_customers_risk', 'customer_risk_scores'],
 'dbt-daily-order-summary': ['daily_order_summary'],
 'dbt-fraud-detection-model': ['stg_fd_orders',
                               'stg_fd_order_lines',
                               'stg_fd_addresses',
                               'fraud_flagged_orders'],
 'dbt-fulfillment-sla': ['int_shipment_times',
                         'fulfillment_sla',
                         'carrier_performance',
                         'warehouse_performance',
                         'fulfillment_summary'],
 'dbt-inventory-analysis': ['fct_warehouse_inventory', 'stg_inventory_levels', 'stg_warehouses'],
 'dbt-product-category-analytics': ['int_product_sales',
                                    'fct_category_performance',
                                    'stg_order_lines',
                                    'stg_products',
                                    'stg_orders',
                                    'stg_categories',
                                    'stg_product_categories'],
 'dbt-retail-task-01-channel-revenue': ['ts_sales__channel_revenue_margin_monthly'],
 'dbt-supplier-scorecard': ['stg_sp__suppliers',
                            'stg_sp__purchase_orders',
                            'stg_sp__purchase_order_receipts',
                            'stg_sp__purchase_order_receipt_lines',
                            'stg_sp__supplier_invoices',
                            'int_po_delivery_performance',
                            'int_po_quality_performance',
                            'int_po_cost_performance',
                            'fct_supplier_metrics',
                            'fct_supplier_scorecard'],
 'dbt-three-way-matching': ['three_way_match']}
PROJECTS = {'dbt-customer-risk-scoring': '/app/dbt_project',
 'dbt-daily-order-summary': '/app/dbt_project',
 'dbt-fraud-detection-model': '/app/dbt_models_duckdb',
 'dbt-fulfillment-sla': '/app/dbt_models_duckdb',
 'dbt-inventory-analysis': '/app/dbt_project',
 'dbt-product-category-analytics': '/app/dbt_project',
 'dbt-retail-task-01-channel-revenue': '/app/dbt_models_duckdb',
 'dbt-supplier-scorecard': '/app/dbt_project',
 'dbt-three-way-matching': '/app/dbt_models_duckdb'}

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def ref(p):return {'path':str(p),'sha256':sha(p)}
def hashes(db):return {x:sha(x)if Path(x).is_file()else None for x in [db,db+'.wal']}
def encode(x):
 if x is None or type(x)in(str,int,bool):return x
 for cls,name in [(datetime.datetime,'datetime'),(datetime.date,'date'),(decimal.Decimal,'decimal'),(float,'float')]:
  if isinstance(x,cls):return {'$type':name,'value':repr(x)if cls is float else str(x)}
 raise TypeError(str(type(x)))
def query(c,sql,path):
 import duckdb
 statements=c.extract_statements(sql)
 if len(statements)!=1 or statements[0].type!=duckdb.StatementType.SELECT:raise ValueError('Single SELECT required')
 types=c.execute('DESCRIBE '+sql).fetchall();cur=c.execute(sql);cols=[r[0]for r in cur.description];count=0
 with path.open('x') as f:
  while batch:=cur.fetchmany(1000):
   for row in batch:f.write(json.dumps([encode(x)for x in row])+'\n');count+=1
 return ref(path)|{'columns':cols,'rows':count,'sql_schema':types,'statement_types':['StatementType.SELECT']}
def collect(task,phase,out,target):
 import duckdb
 if task not in RAW or duckdb.__version__!='1.2.2':raise ValueError('Unsupported task or database version')
 project=Path(PROJECTS[task]);mp=project/target/'manifest.json';db='/app/database/retail.duckdb'
 if Path(target).is_absolute()or'..'in Path(target).parts:raise ValueError('Unsafe target')
 out.mkdir(parents=True,exist_ok=False)
 now=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat()
 result={'task_key':'deb__'+task,'kind':task,'phase':phase,'collector':ref(__file__),'started_at_utc':now(),'project_exists':project.exists(),'project_root':str(project),'raw':{},'compiled':{},'physical_outputs':{},'compiled_errors':{},'database_hashes_before':hashes(db),'environment':{k:os.environ.get(k)for k in ['DB_TYPE','DUCKDB_PATH','DBT_PROFILES_DIR']}}
 try:
  with duckdb.connect(db,read_only=True)as c:
   for name,sql in RAW[task].items():result['raw'][name]=query(c,sql,out/(name+'.jsonl'))|{'source_sql':sql}
   if phase=='final':
    manifest=json.loads(mp.read_text());result['manifest']=ref(mp)
    for uid,node in manifest['nodes'].items():
     if node.get('resource_type')!='model' or node['name']not in MODELS[task]:continue
     name=node['name'];sql=node.get('compiled_code')
     if not isinstance(sql,str):result['compiled_errors'][name]={'reason':'Actual model has no compiled code'};continue
     sqlpath=out/(name+'.sql');sqlpath.write_text(sql)
     try:result['compiled'][name]=query(c,sql,out/(name+'-compiled.jsonl'))|{'sql':ref(sqlpath),'unique_id':uid,'original_file_path':node['original_file_path'],'manifest_invocation_id':manifest['metadata']['invocation_id']}
     except Exception as e:result['compiled_errors'][name]={'type':type(e).__name__,'message':str(e),'sql':ref(sqlpath)}
     if node.get('config',{}).get('materialized')=='ephemeral':continue
     schema,alias=node['schema'],node.get('alias',name)
     try:result['physical_outputs'][name]=query(c,'SELECT * FROM "'+schema.replace('"','""')+'"."'+alias.replace('"','""')+'"',out/(name+'-physical.jsonl'))
     except Exception as e:result.setdefault('physical_errors',{})[name]={'type':type(e).__name__,'message':str(e)}
 except Exception as e:result['capture_error']={'type':type(e).__name__,'message':str(e)}
 result['database_hashes_after']=hashes(db);result['database_and_wal_unchanged']=result['database_hashes_before']==result['database_hashes_after'];result['finished_at_utc']=now()
 result['model_requests']=0;result['scope']='Original fixed raw projections and actual current compiled SELECTs; no reference source substitution. Actual model commands are attested separately.'
 (out/'capture.json').write_text(json.dumps(result,indent=2)+'\n');return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--phase',choices=['initial','final'],required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--target-path',default='target');a=p.parse_args();r=collect(a.task,a.phase,a.output,a.target_path);print(json.dumps({'capture_error':r.get('capture_error'),'raw':list(r['raw']),'compiled':list(r['compiled']),'model_requests':0}))
