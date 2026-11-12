import unittest
from datetime import date,datetime
from decimal import Decimal as D
from dataclasses import replace
from phase4_deb_recompute_v2 import warning,cohorts,rfm2,nearest,numbers_equal,Rounded,ntile,compare_outputs
from audit_tables import CapturedTable

class RecomputeTests(unittest.TestCase):
    def test_rounding_is_not_blanket_one_cent(self):
        self.assertEqual(nearest(D('1.005')),{D('1.00'),D('1.01')})
        self.assertTrue(numbers_equal(D('1.01'),Rounded(D('1.005'))))
        self.assertFalse(numbers_equal(D('1.01'),Rounded(D('1.0049'))))
        self.assertFalse(numbers_equal(D('1.02'),Rounded(D('1.005'))))
        self.assertFalse(numbers_equal(float('nan'),D(0)))
        self.assertFalse(numbers_equal(True,D(1)))
    def test_ntile_remainder_and_large_identifier_are_exact(self):
        ids=[9007199254740992+i for i in range(7)]
        x=ntile([(1,k) for k in reversed(ids)])
        self.assertEqual([x[k] for k in ids],[1,1,2,2,3,4,5])
    def test_warning_drops_null_dates_before_population_and_money(self):
        base={'order_id':'one','customer_id':'A','ordered_at':datetime(2024,12,31),'grand_total':D('2.00')}
        rows=[base,dict(base,order_id='two',ordered_at=None,grand_total=D('100')),dict(base,customer_id='B',ordered_at=None),dict(base,customer_id=None,ordered_at=datetime(2030,1,1))]
        out,facts=warning({'enriched':rows});r=out['rpt_customer_churn_early_warning_fixed']
        self.assertEqual(len(r),1);self.assertEqual(r[0]['lifetime_orders'],1);self.assertEqual(r[0]['lifetime_revenue'],D('2.00'))
        self.assertEqual(r[0]['week_start'],date(2024,12,30));self.assertEqual(facts['customers'],1)
    def test_cohort_calendar_gap_and_signed_signup_are_retained(self):
        raw={'dim_customer':[{'customer_key':1,'customer_id':'A'}],
             'dim_date':[{'date_key':1,'full_date':date(2024,1,1)},{'date_key':3,'full_date':date(2024,3,1)}],
             'customers':[{'customer_id':'A','acquisition_date':date(2024,2,1),'legacy_region_code':None}],
             'sales':[{'customer_key':1,'date_key':1,'total_amount':D(12)},{'customer_key':1,'date_key':3,'total_amount':D(6)}]}
        out,facts=cohorts(raw)
        self.assertEqual(out['customer_cohorts'][0]['signup_to_first_purchase_days'],-31)
        self.assertEqual([r['active_customers'] for r in out['cohort_retention']],[1,0,1])
        self.assertEqual([r['retained_customers'] for r in out['cohort_retention']],[1,0,0])
        self.assertEqual(out['customer_clv'][0]['customer_lifespan_months'],3)
    def test_missing_customer_column_is_scored_as_failure_not_exception(self):
        raw={'orders':[{'customer_id':'A','order_id':'o','status':'COMPLETE','ordered_at':datetime(2024,1,1),'grand_total':D(5)}]}
        want,_=rfm2(raw,{'rfm_segments':[{'lifetime_value_estimate':0}]})
        table=CapturedTable(('total_orders',),{'total_orders':'BIGINT'},[{'total_orders':1}],('total_orders',),{'total_orders':'BIGINT'},'BASE TABLE',{})
        result=compare_outputs({'rfm_segments':{'table':table,'rows':table.rows}},want)
        self.assertEqual(result['reward'],0)

if __name__=='__main__':unittest.main()
