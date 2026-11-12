"""Declared typed output policies and independent current-input row comparisons."""
from decimal import Decimal
from audit_tables import CapturedTable, TablePolicy, audit_table
from dbt_bench.comparison import Column, TableContract, compare_tables

INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMERIC=INTS+('FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})' for p in range(1,39) for s in range(p+1))
KEYS={'fraud_flagged_orders':('order_id',),'int_shipment_times':('shipment_id',),'fulfillment_sla':('fulfillment_month','shipping_method_id','warehouse_id'),'carrier_performance':('carrier_id',),'warehouse_performance':('warehouse_id',),'fulfillment_summary':('carrier_name','service_tier')}
ORDER={'fraud_flagged_orders':('order_id',),'fulfillment_sla':('fulfillment_month','warehouse_name','shipping_method_name'),'carrier_performance':('carrier_rank',),'warehouse_performance':('warehouse_rank',),'fulfillment_summary':('carrier_name','service_tier')}
INTERMEDIATE=tuple('shipment_id order_id ordered_at shipped_at delivered_at grand_total shipping_method_id warehouse_id carrier_id shipping_method_name warehouse_name carrier_name estimated_days_min estimated_days_max is_express processing_days transit_days total_days value_tier on_time_status breach_severity warehouse_delay carrier_delay delay_attribution'.split())
TEXT=set('order_id customer_id shipment_id warehouse_id shipping_method_id carrier_id billing_state shipping_state fraud_risk_level fulfillment_month shipping_method_name warehouse_name carrier_name carrier_code carrier_type warehouse_code warehouse_type state_province country_code primary_carrier_name performance_tier recent_trend service_tier value_tier_distribution value_tier on_time_status breach_severity delay_attribution'.split())
BOOLS=set('is_high_value is_bulk_order is_velocity_fraud is_address_mismatch is_first_order_high_value has_chargeback_history'.split())
FLAGS={'is_express','is_peak_month','warehouse_delay','carrier_delay'}
TIMESTAMPS={'ordered_at','shipped_at','delivered_at'}
DATES={'first_shipment_date','last_shipment_date'}

def check(status,reason,**x):return {'status':status,'reason':reason,**x}

def columns(name, expected):
    # Independent expected schema is defined by instruction formulas, never candidate columns.
    return INTERMEDIATE if name=='int_shipment_times' else tuple(expected[0])

def contract(name, expected, ordered=False):
    return TableContract(tuple(Column(c,'text' if c in TEXT else 'boolean' if c in BOOLS else 'datetime' if c in TIMESTAMPS else 'date' if c in DATES else 'numeric') for c in columns(name,expected)),keys=KEYS[name],ordered=ordered,allow_extra_columns=True)

def policy(name, expected):
    names=columns(name,expected)
    types={c:('VARCHAR',) if c in TEXT else ('BOOLEAN',) if c in BOOLS else ('TIMESTAMP','TIMESTAMP_S','TIMESTAMP_MS','TIMESTAMP_NS') if c in TIMESTAMPS else ('DATE',) if c in DATES else INTS if c in FLAGS else NUMERIC for c in names}
    relation_types=() if name=='fraud_flagged_orders' else ('VIEW',) if name=='int_shipment_times' else ('TABLE','BASE TABLE')
    return TablePolicy(contract(name,expected),types,not_null=KEYS[name],relation_types=relation_types)

def normalize(t):
    return CapturedTable(tuple(c.lower() for c in t.columns),{c.lower():v for c,v in t.sql_types.items()},[{c.lower():v for c,v in r.items()} for r in t.rows],tuple(c.lower() for c in t.physical_columns),{c.lower():v for c,v in t.physical_sql_types.items()},t.relation_type,t.capture_ref)

def integral_column(c):
    return c in FLAGS or c in ('total_items','orders_same_day','fraud_flags_count','total_shipments','unique_orders','active_months','distinct_carriers','min_total_days','max_total_days','processing_days','transit_days','total_days','estimated_days_min','estimated_days_max') or c.endswith(('_count','_shipments','_rank')) or c=='rank_within_type'

def decimals(c):
    if c in ('grand_total','total_order_value','avg_shipment_value'):return 2
    if c.endswith('_score') or c.endswith(('_pct','_rate','_sla_diff')):return 1
    if c.startswith(('avg_','p50_','p75_','p90_','p95_')) or c.endswith('_std_dev'):return 2
    return None

def domain_errors(name, rows, expected):
    errors=[]
    for i,r in enumerate(rows):
        for c in columns(name,expected):
            v=r.get(c)
            if v is None or c in TEXT|BOOLS|TIMESTAMPS|DATES:continue
            if type(v) not in (int,float,Decimal):errors.append((i,c,'not numeric'));continue
            d=Decimal(str(v))
            if not d.is_finite():errors.append((i,c,'not finite'));continue
            if integral_column(c) and d!=d.to_integral_value():errors.append((i,c,'nonintegral count/flag/day'))
            if c in FLAGS and d not in (0,1):errors.append((i,c,'flag outside 0/1'))
            n=None if name=='int_shipment_times' and c=='grand_total' else decimals(c)
            if n is not None and d!=d.quantize(Decimal(1).scaleb(-n)):errors.append((i,c,'exceeds required precision'))
    return errors

def evaluate(kind, outputs, expected_variants, *, workflow=None, compiled=None):
    checks={};baseline=expected_variants[0]
    for name,expected in baseline.items():
        o=outputs.get(name,{})
        if o.get('exists') is False:checks[name+'.presence']=check('fail','Trusted capture explicitly proves required relation absent')
        rows=o.get('rows');t=o.get('table')
        if rows is None:checks[name+'.values']=check('pending','Lossless output rows unavailable');continue
        rows=[{c.lower():v for c,v in r.items()}for r in rows]
        candidates=[compare_tables(v[name],rows,contract(name,v[name])) for v in expected_variants]
        checks[name+'.values']=check('pass' if any(x.equal for x in candidates) else 'fail','Independent raw-input formula comparison with exact midpoint choices only',comparisons=[x.reason for x in candidates])
        errs=domain_errors(name,rows,expected);checks[name+'.domains']=check('fail'if errs else'pass','Finite numeric values, integral counts/flags/day intervals and declared decimal precision',examples=errs[:10],error_count=len(errs))
        if t is None:checks[name+'.structure']=check('pending','Exact native SQL and physical relation metadata unavailable')
        else:
            audit=audit_table(normalize(t),policy(name,expected));checks[name+'.structure']=check('pending'if audit['status']=='unknown'else'pass'if audit['reward']==1 else'fail','Declared typed schema/grain/materialization',details=audit)
        # Projection types can independently disprove a rule even if old physical metadata is absent.
        wrong={c:tp for c,tp in o.get('sql_types',{}).items()if c in policy(name,expected).sql_types and tp not in policy(name,expected).sql_types[c]}
        if wrong:checks[name+'.captured_types']=check('fail','Captured SQL types violate task type families',wrong=wrong)
        if name in ORDER:
            actual=(compiled or {}).get(name)
            if actual is None:checks[name+'.order']=check('pending','Actual compiled SELECT sequence unavailable; physical scan order is not used')
            else:
                actual=[{c.lower():v for c,v in r.items()}for r in actual]
                ok=any(compare_tables(v[name],actual,contract(name,v[name],True)).equal for v in expected_variants)
                checks[name+'.order']=check('pass'if ok else'fail','Actual compiled SELECT complete rows in declared ordering, compared to independent sorted rows')
    checks['workflow']=workflow or check('pending','Trusted source/profile/process/backend capture unavailable')
    states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if'pending'in states or'gap'in states else 1
    return {'status':'unknown'if reward is None else'scored','reward':reward,'checks':checks,'model_requests':0,'native_rewards_modified':False}
