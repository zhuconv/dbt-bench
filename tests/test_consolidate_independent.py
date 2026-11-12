import importlib.util
from pathlib import Path
import unittest

SPEC=importlib.util.spec_from_file_location('consolidate_independent',Path(__file__).resolve().parents[1]/'validation/deb/dbt-consolidate/independent.py')
independent=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(independent)


class ConsolidateDerivationTests(unittest.TestCase):
    def inputs(self):
        base={'ad_date':'2025-12-01','clicks':'3','impressions':'17','conversions':'1'}
        return {'google':[{**base,'views':'9'}],
                'meta':[{**base,'views_1':'7','views_2':'12'}],
                'tiktok':[{**base,'views_1':'9'}]}

    def test_hand_computable_platform_mapping_and_union(self):
        result=independent.derive(self.inputs())
        self.assertEqual(result['meta'],[('2025-12-01',3,17,19,1)])
        self.assertEqual(result['unified'],[
            ('google','2025-12-01',3,17,9,1),('meta','2025-12-01',3,17,19,1),
            ('tiktok','2025-12-01',3,17,9,1)])

    def test_deduplication_retains_distinct_content_on_same_date(self):
        data=self.inputs()
        data['tiktok'] += [dict(data['tiktok'][0]), {**data['tiktok'][0],'clicks':'4'}]
        self.assertEqual(independent.derive(data)['tiktok'],[
            ('2025-12-01',3,17,9,1),('2025-12-01',4,17,9,1)])

    def test_integral_decimal_representation_is_exact_without_float_ids(self):
        self.assertEqual(independent.count('10.00'),10)
        self.assertEqual(independent.count('9007199254740993.00'),9007199254740993)
        for bad in ['10.25','NaN','Infinity',True,None]:
            with self.subTest(value=bad),self.assertRaises(ValueError):independent.count(bad)

    def test_row_associations_and_duplicates_are_not_lost(self):
        expected=[('2025-12-01',3,17,9,1),('2025-12-02',4,18,10,2)]
        actual=[dict(zip(independent.STAGING_COLUMNS,r)) for r in expected]
        actual[0]['clicks'],actual[1]['clicks']=actual[1]['clicks'],actual[0]['clicks']
        self.assertFalse(independent.compare(expected,actual)['equal'])
        correct=[dict(zip(independent.STAGING_COLUMNS,r)) for r in expected]
        self.assertTrue(independent.compare(expected,list(reversed(correct)))['equal'])
        self.assertFalse(independent.compare(expected,correct+[correct[0]])['equal'])
