import importlib.util
from pathlib import Path
import unittest
from decimal import Decimal

BASE=Path(__file__).resolve().parents[1]/'validation/deb/dbt-session-attribution/semantics'
def module(name):
    spec=importlib.util.spec_from_file_location('session_'+name,BASE/(name+'.py'));obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj
sem=module('independent');fixtures=module('fixtures')


class SessionSemanticsTests(unittest.TestCase):
    def test_priority_and_exact_source_presence(self):
        s={'utm_medium':'CpC','referrer':'facebook.google.mail','utm_source':'ad'}
        self.assertEqual(sem.channel(s),'Paid Search')
        s['utm_medium']=None;self.assertEqual(sem.channel(s),'Paid Social')
        s['utm_source']='';self.assertEqual(sem.channel(s),'Organic Search')
        s['referrer']='facebook.mail';self.assertEqual(sem.channel(s),'Organic Social')

    def test_hand_computable_boundaries_and_single_touch(self):
        data=fixtures.fixture();data['sessions']=[s for s in data['sessions'] if s['customer_id']=='boundary']
        result=sem.calculate(data)
        self.assertEqual(result['expected'],[{'channel':'Email','total_conversions':'1.00','total_revenue':'300.00'}])
        self.assertEqual(result['coverage']['exact_lower_boundary'],1)
        self.assertEqual(len(result['paths'][0]['touch_ids']),1)

    def test_three_touch_weight_ratio_and_revenue_bonus(self):
        data=fixtures.fixture();data['sessions']=[s for s in data['sessions'] if s['customer_id']=='three']
        path=sem.calculate(data)['paths'][0];credits=list(map(Decimal,path['credits']))
        self.assertTrue(path['bonus']);self.assertEqual(Decimal(path['adjusted_revenue']),Decimal(132))
        self.assertLess(abs(sum(credits)-1),Decimal('1e-25'))
        # Independently evaluate ratio w(first)/w(last) = (1.5/1.3)*exp(-9.5/7).
        import math
        self.assertAlmostEqual(float(credits[0]/credits[2]),1.5/1.3*math.exp(-9.5/7),places=12)
        self.assertGreater(credits[2],credits[1]);self.assertGreater(credits[1],credits[0])

    def test_fixture_branch_coverage_and_quality(self):
        r=sem.calculate(fixtures.fixture());self.assertFalse(r['unresolved'])
        self.assertEqual(r['coverage']['matching_conversions'],24)
        self.assertEqual(r['coverage']['single_touch'],18);self.assertEqual(r['coverage']['zero_touch'],1)
        self.assertEqual(r['coverage']['two_touch'],3);self.assertEqual(r['coverage']['multi_touch'],2)
        self.assertEqual(r['coverage']['bonus_paths'],1)
        self.assertEqual(r['coverage']['bot'],1);self.assertEqual(r['coverage']['low_quality'],2)

    def test_duplicate_association_null_and_extra_columns(self):
        exp=[{'channel':'A','total_conversions':'1.00','total_revenue':'10.00'},{'channel':'B','total_conversions':'2.00','total_revenue':'20.00'}]
        actual={'columns':list(exp[0])+['extra'],'rows':[dict(r,extra=42) for r in exp[::-1]]}
        self.assertTrue(sem.compare(exp,actual)['equal'])
        actual['rows'].append(actual['rows'][0]);self.assertFalse(sem.compare(exp,actual)['equal'])
        actual['rows']=[dict(exp[0],total_revenue='20'),dict(exp[1],total_revenue='10')]
        self.assertEqual(len(sem.compare(exp,actual)['mismatches']),2)
        actual['rows']=[dict(exp[0],total_conversions=None),exp[1]]
        self.assertFalse(sem.compare(exp,actual)['equal'])

    def test_ambiguous_input_is_explicit(self):
        data=fixtures.fixture();s=data['sessions'][0];s['device_type']=None
        self.assertTrue(any(x.startswith('null_device:') for x in sem.calculate(data)['unresolved']))
