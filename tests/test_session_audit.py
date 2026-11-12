import copy
from datetime import datetime, timedelta
import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'validation/deb/dbt-session-attribution/audit_v1/checker.py'
spec = importlib.util.spec_from_file_location('session_audit', PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture():
    end = datetime(2026, 1, 15)
    def session(key, days, medium, converted=False):
        return dict(session_id=key, customer_id='9007199254740993',
                    session_start=str(end - timedelta(days=days)),
                    referrer='', utm_source='', utm_medium=medium, device_type='desktop',
                    duration_seconds=5, page_views=2, is_converted=converted,
                    order_id='9007199254740993' if converted else None)
    return {'sessions': [session('first',14,'CPC'), session('last',7,'email'), session('conversion',0,'',True)],
            'sales': [{'order_id':'9007199254740993','total_amount':'100.00'}]}


def output(rows):
    return dict(columns=list(audit.REQUIRED), rows=copy.deepcopy(rows), relation_type='BASE TABLE')


class SessionAuditTests(unittest.TestCase):
    def test_scaled_decay_matches_independent_two_touch_closed_form_at_boundary(self):
        result = audit.recompute(fixture())
        first = (1.5 / math.e) / (1.5 / math.e + 1.3)
        wanted = {'Paid Search': (round(first, 2), round(100 * first, 2)),
                  'Email': (round(1-first, 2), round(100 * (1-first), 2))}
        self.assertEqual(result['unknown'], [])
        for row in result['expected']:
            self.assertEqual((float(row['total_conversions']),float(row['total_revenue'])), wanted[row['channel']])
        self.assertEqual(result['coverage']['touches_2'], 1)

    def test_bonus_only_changes_revenue_and_sales_lines_are_summed(self):
        data = fixture();end = datetime(2026,1,15)
        data['sessions'][0]['session_start'] = str(end - timedelta(seconds=3))
        data['sessions'][1]['session_start'] = str(end - timedelta(seconds=1))
        middle = dict(data['sessions'][0], session_id='middle', utm_medium='', referrer='google',
                      session_start=str(end - timedelta(seconds=2)))
        data['sessions'].append(middle)
        data['sales'] = [{'order_id':'9007199254740993','total_amount':'40'},
                         {'order_id':'9007199254740993','total_amount':'60'}]
        result = audit.recompute(data)
        self.assertEqual(result['coverage']['bonus_paths'], 1)
        self.assertEqual({r['channel']:(r['total_conversions'],r['total_revenue']) for r in result['expected']},
                         {'Paid Search':('0.39','43.42'), 'Organic Search':('0.26','28.95'), 'Email':('0.34','37.63')})

    def test_exact_large_order_ids_and_strict_conversion_upper_boundary(self):
        data = fixture();data['sales'][0]['order_id'] = '9007199254740992'
        self.assertEqual(audit.recompute(data)['expected'], [])
        data = fixture();data['sessions'][1]['session_start'] = data['sessions'][2]['session_start']
        result = audit.recompute(data)
        self.assertEqual(result['expected'], [{'channel':'Paid Search','total_conversions':'1.00','total_revenue':'100.00'}])

    def test_channel_priority_quality_and_unknown_branches(self):
        s = dict(fixture()['sessions'][0], referrer='FACEBOOK Google mail', utm_source='x', utm_medium='PPC')
        self.assertEqual(audit.classify(s), 'Paid Search')
        s['utm_medium'] = ''; self.assertEqual(audit.classify(s), 'Paid Social')
        for field,value in [('duration_seconds',4),('page_views',1),('device_type','BoT')]:
            d = fixture();d['sessions'][0][field] = value
            self.assertEqual(audit.recompute(d)['expected'], [{'channel':'Email','total_conversions':'1.00','total_revenue':'100.00'}])
        for alteration in ['null_device','tied_touch','repeated_converting_order','half_cent_rounding_unspecified']:
            d = fixture()
            if alteration=='null_device':d['sessions'][0]['device_type']=None
            elif alteration=='tied_touch':d['sessions'][0]['session_start']=d['sessions'][1]['session_start']
            elif alteration=='repeated_converting_order':d['sessions'].append(dict(d['sessions'][-1],session_id='another_conversion'))
            else:d['sessions'].pop(0);d['sales'][0]['total_amount']='0.005'
            self.assertIn(alteration,{r['kind'] for r in audit.recompute(d)['unknown']})

    def test_duplicate_keys_value_association_precision_and_optional_columns(self):
        rows = audit.recompute(fixture())['expected'];good=output(rows[::-1])
        good['columns'].append('optional');good['rows'][0]['optional']=None
        self.assertEqual(audit.compare(rows,good)['status'],'pass')
        bad=copy.deepcopy(good);bad['rows'].append(bad['rows'][0])
        self.assertEqual(audit.compare(rows,bad)['status'],'fail')
        bad=output(rows);bad['rows'][0]['total_conversions']=str(audit.decimal(bad['rows'][0]['total_conversions'])+audit.Decimal('.001'))
        self.assertEqual(audit.compare(rows,bad)['status'],'fail')
        bad=output(rows);bad['rows'][0]['channel'],bad['rows'][1]['channel']=bad['rows'][1]['channel'],bad['rows'][0]['channel']
        self.assertEqual(audit.compare(rows,bad)['status'],'fail')
        self.assertEqual(audit.compare(rows,None)['status'],'fail')
        for value in [None,'NaN','Infinity',True]:
            bad=output(rows);bad['rows'][0]['total_revenue']=value
            self.assertEqual(audit.compare(rows,bad)['status'],'fail')

    def test_workflow_obligations_are_independent_of_correct_values(self):
        rows=audit.recompute(fixture())['expected']
        state=dict(model_present=True,model_sha256='same',required_output=output(rows),
                   staging_sources={'models/staging/existing.sql':'original'},query_channels=sorted(r['channel'] for r in rows))
        self.assertEqual(audit.workflow(state,[state,state],rows)['status'],'pass')
        self.assertEqual(audit.workflow(None,[],rows)['status'],'unknown')
        self.assertEqual(audit.workflow(state,[state],rows)['status'],'unknown')
        for field,value,issue in [('model_present',False,'required_model_absent'),
                                  ('query_channels',list(reversed(state['query_channels'])),'compiled_query_not_ascending')]:
            final=copy.deepcopy(state);final[field]=value
            self.assertIn(issue,audit.workflow(state,[final],rows)['issues'])
        final=copy.deepcopy(state);final['required_output']['relation_type']='VIEW'
        self.assertIn('table_materialization_required',audit.workflow(state,[final],rows)['issues'])
        final=copy.deepcopy(state);final['staging_sources']['models/staging/new.sql']='new'
        self.assertIn('new_staging_models',audit.workflow(state,[final],rows)['issues'])
        final=copy.deepcopy(state);final['required_output']['rows']*=2
        self.assertIn('repeated_build_not_stable_and_correct',audit.workflow(state,[state,final],rows)['issues'])

    def test_original_capture_hash_cannot_be_rebound_after_export_edit(self):
        sys.path.insert(0,str(ROOT/'src'))
        spec=importlib.util.spec_from_file_location('session_audit_capture',PATH.with_name('capture.py'))
        capture=importlib.util.module_from_spec(spec);spec.loader.exec_module(capture)
        with tempfile.TemporaryDirectory() as tmp:
            agent=Path(tmp);p=agent/'phase/output.json';p.parent.mkdir();p.write_text('{"value":1}')
            reference={'path':'/logs/agent/phase/output.json','sha256':audit.sha(p)}
            self.assertEqual(capture.exported(agent,reference,'phase'),p)
            p.write_text('{"value":2}')
            with self.assertRaisesRegex(ValueError,'export changed'):capture.exported(agent,reference,'phase')
            with self.assertRaises(ValueError):audit.verified(agent,{'path':'../outside','sha256':'0'*64})


if __name__=='__main__':unittest.main()
