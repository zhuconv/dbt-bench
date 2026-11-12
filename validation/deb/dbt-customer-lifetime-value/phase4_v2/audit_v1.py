"""Full seven-model CLV audit with declared rounding/default interpretation."""
from decimal import Decimal as D, ROUND_HALF_UP, ROUND_HALF_DOWN, ROUND_HALF_EVEN
from copy import deepcopy
import math
from expected_v1 import derive
from common import ck,finish,table_check,NUMS,INTS
COLS={'clv_segments':'customer_id customer_name first_order_date last_order_date customer_tenure_days days_since_last_order total_orders total_revenue avg_order_value avg_days_between_orders orders_per_year predicted_annual_revenue clv_3_year clv_3_year_npv clv_segment is_at_risk clv_percentile clv_quartile lifecycle_stage first_order_quarter value_trajectory velocity_trend churn_probability_score revenue_contribution_pct cumulative_revenue_pct revenue_rank engagement_score expected_next_order_date days_until_expected_order days_overdue'.split(),'clv_segment_summary':'clv_segment customer_count total_revenue avg_clv_3_year avg_clv_3_year_npv avg_orders_per_year at_risk_count at_risk_percentage churned_count avg_tenure_days avg_engagement_score avg_churn_probability accelerating_count decelerating_count pct_revenue_contribution'.split(),'clv_cohort_analysis':'cohort_quarter cohort_size total_cohort_revenue avg_clv_3_year avg_clv_3_year_npv avg_orders retention_rate at_risk_rate avg_engagement_score avg_churn_probability platinum_count gold_count silver_count bronze_count accelerating_pct decelerating_pct'.split(),'clv_monthly_trends':'customer_id order_month monthly_orders monthly_revenue cumulative_orders cumulative_revenue months_since_first_order avg_monthly_revenue is_active_month order_month_rank'.split()}
KEYS={'clv_segments':['customer_id'],'clv_segment_summary':['clv_segment'],'clv_cohort_analysis':['cohort_quarter'],'clv_monthly_trends':['customer_id','order_month']}
TEXT=set('customer_id customer_name clv_segment lifecycle_stage first_order_quarter value_trajectory velocity_trend cohort_quarter order_month'.split());COUNTS=set('customer_tenure_days days_since_last_order total_orders clv_quartile revenue_rank days_until_expected_order days_overdue customer_count at_risk_count churned_count accelerating_count decelerating_count cohort_size platinum_count gold_count silver_count bronze_count monthly_orders cumulative_orders months_since_first_order order_month_rank'.split());BOOLS={'is_at_risk','is_active_month'};TIMES={'first_order_date','last_order_date'};ONE=set('avg_days_between_orders clv_percentile churn_probability_score revenue_contribution_pct cumulative_revenue_pct engagement_score at_risk_percentage avg_tenure_days avg_engagement_score avg_churn_probability pct_revenue_contribution retention_rate at_risk_rate accelerating_pct decelerating_pct'.split());INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')

MODES=(ROUND_HALF_UP,ROUND_HALF_DOWN,ROUND_HALF_EVEN)
LIFECYCLES={'New','Growing','Mature','Declining','Churned','At Risk - High Value'}

def evaluate(outputs, expected, workflow=None):
    raw=expected['raw'];base=derive(raw);holes={r['customer_id'] for r in base['profile']['undefined_conditions']}
    actual={r.get('customer_id'):r for r in outputs.get('clv_segments',{}).get('rows',[])}
    fallback={cid:actual.get(cid,{}).get('lifecycle_stage') for cid in holes}
    errors=[cid for cid,value in fallback.items() if value not in LIFECYCLES]
    variants=[]
    for mode in MODES:
        values=derive(raw,mode,fallbacks={k:v for k,v in fallback.items() if v in LIFECYCLES});checks={}
        checks['default_scope']=ck('fail' if errors else 'pass','Defaults apply only to original-input customers matching none of the ordered lifecycle predicates. Default must be one declared enum; aggregates are recomputed using that actual permitted choice.',eligible_customer_ids=sorted(holes),actual_defaults=fallback,errors=errors)
        for name,columns in COLS.items():
            rows=values['outputs']['main.'+name]
            types={c:('VARCHAR',) if c in TEXT else INTS if c in COUNTS else ('BOOLEAN',) if c in BOOLS else ('TIMESTAMP','TIMESTAMP WITH TIME ZONE') if c in TIMES else ('DATE',) if c=='expected_next_order_date' else tuple(t for t in NUMS if t.startswith('DECIMAL(')) for c in columns}
            checks[name]=table_check(outputs.get(name,{}),rows,KEYS[name],required=columns,materializations=('TABLE','BASE TABLE'),not_null=columns if name=='clv_segments' else (),types=types)
            precision=[]
            for r in outputs.get(name,{}).get('rows',[]):
                for c in columns:
                    v=r.get(c)
                    if c in TEXT|COUNTS|BOOLS|TIMES or c=='expected_next_order_date' or v is None:continue
                    places=1 if c in ONE else 2
                    if type(v) in (D,int,float) and D(str(v)).is_finite() and D(str(v)) != D(str(v)).quantize(D(1).scaleb(-places)):precision.append([c,str(v),places])
            checks[name+'.precision']=ck('fail' if precision else 'pass','Declared per-column precision, no global approximate numeric comparison.',errors=precision[:10])
            sequence=[tuple(r.get(c) for c in (['avg_clv_3_year'] if name=='clv_segment_summary' else KEYS[name])) for r in outputs.get(name,{}).get('rows',[])]
            try:ordered=all(a>=b for a,b in zip(sequence,sequence[1:])) if name=='clv_segment_summary' else all(a<=b for a,b in zip(sequence,sequence[1:]))
            except TypeError:ordered=False
            checks[name+'.order']=ck('pass' if ordered else 'fail','Explicit task ordering, no additional tied-row ordering.')
        # Intermediate schemas are not enumerated by the instruction. Require
        # explicit per-order fields and grain; compare every recognized exposed
        # metric by its task-defined meaning without imposing author helper names.
        for name,keys,minimum in [('int_clv_customer_orders',('order_id',),'order_id customer_id ordered_at grand_total days_since_previous_order'.split()),('int_clv_metrics',('customer_id',),['customer_id']),('clv_predictions',('customer_id',),['customer_id'])]:
            item=outputs.get(name,{});rows=values['outputs']['main.'+name];present=set(item.get('table').columns) if item.get('table') else set()
            projection=sorted(set(minimum)|(present & set(rows[0])))
            tolerance={}
            for col in ('avg_days_between_orders_raw','stddev_days_between_orders','recent_avg_days','historical_avg_days'):
                nums=[r.get(col) for r in rows if r.get(col) is not None]
                if nums:tolerance[col]=D.from_float(max(math.ulp(float(n)) for n in nums))*16
            checks[name]=table_check(item,rows,keys,required=projection,materializations=('VIEW',),tolerance=tolerance)
            if name=='int_clv_metrics':checks[name+'.meaning']=ck('pass' if (present & set(rows[0]))-{'customer_id'} else 'fail','Customer metrics model exposes independently validated customer aggregate(s).')
            if name=='clv_predictions':checks[name+'.meaning']=ck('pass' if 'clv_3_year' in present and present & {'is_at_risk','churn_probability_score','engagement_score','lifecycle_stage'} else 'fail','Prediction model exposes a CLV projection and health indicator, with all exposed contract columns independently checked.')
        checks['workflow']=workflow or ck('pending','Actual seven-model source/build evidence absent')
        ans=finish(checks);ans['rounding_policy']=mode;variants.append(ans)
    passed=[v for v in variants if v['reward']==1]
    result=passed[0] if passed else min(variants,key=lambda v:sum(x['status']=='fail' for x in v['checks'].values()))
    result['rounding_scope']={'allowed_modes':list(MODES),'matched_modes':[v['rounding_policy'] for v in passed], 'rule':'All calculations replay nearest rounding with the same conventional tie rule, feeding rounded prior values into downstream formulas; lifecycle defaults are applied before aggregates. No candidate value is accepted outside an independently derived branch.'}
    return result
