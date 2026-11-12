import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from propose_difficulty_split import workflow_group,difficulty_group,propose


class DifficultySplitTest(unittest.TestCase):
    def test_exact_user_workflow_mapping(self):
        for v in ('new_project','new_project_from_reference_models'):
            self.assertEqual(workflow_group(v),'new_project')
        for v in ('diagnose_and_add_model','repair grain','build models and repair grain',
                  'modify_existing_model_and_macros','rebuild attribution_model'):
            self.assertEqual(workflow_group(v),'repair_diagnosis')
        self.assertEqual(workflow_group('complete_or_fix_project_run_all_models'),'add_or_complete_models')

    def test_difficulty_keeps_zero_and_refuses_unknown_boolean(self):
        self.assertEqual([difficulty_group(n) for n in range(5)],['0','1-3','1-3','1-3','4'])
        for n in (None,True,5,1.0):
            with self.assertRaises(ValueError):difficulty_group(n)

    def test_family_intact_forced_by_candidates_not_size_reproducible(self):
        rows=[]
        for family,size in [('large',6),('sparse',7),('empty',3),('single',1)]:
            rows.extend({'task_key':family+str(i),'task_family':family,'source':'deb',
                         'workflow':'new_project','split':'train'} for i in range(size))
        counts={**{'large'+str(i):i%5 for i in range(5)},'sparse0':4,'single0':0}
        a=propose(rows,counts,seed=2026110801)
        b=propose(list(reversed(rows)),dict(reversed(list(counts.items()))),seed=2026110801)
        self.assertEqual(a,b)
        self.assertEqual(a['forced_train_families'],['large'])
        self.assertEqual(a['candidate_count'],7)
        for group in a['groups']:
            self.assertEqual({a['assignments'][k] for k in group['task_keys']},{group['split']})
        self.assertFalse(a['frozen']);self.assertEqual(a['draws'],10000)
        self.assertEqual(sum(a['candidate_split_counts'].values()),7)

    def test_source_fraction_denominator_is_all_candidates_of_category(self):
        rows=[{'task_key':str(i),'task_family':str(i),'source':'ade' if i<4 else 'deb',
               'workflow':'add_models','split':'train'} for i in range(8)]
        r=propose(rows,{str(i):4 for i in range(8)},seed=2026110801)
        self.assertEqual(r['objective']['numerator'],0)
        self.assertEqual(r['strata']['source']['ade']['test'],1)
        self.assertEqual(r['strata']['source']['deb']['test'],1)

    def test_no_shortened_search_or_unknown_candidate(self):
        with self.assertRaises(ValueError):propose([],{'missing':4},seed=1)
        with self.assertRaises(ValueError):propose([],{},seed=1,draws=20)

    def test_exact_draw_count_first_minimum_and_shared_noncandidate_stream(self):
        # A controlled random stream has its first optimum at draw 3 and the
        # same optimum again at draw 5. All other draws assign no candidates
        # to test. This tests selection behavior without drawing a real pool.
        rows=[{'task_key':'c'+str(i),'task_family':'f'+str(i),'source':'ade',
               'workflow':'add_models','split':'train'} for i in range(8)]
        rows += [{'task_key':'empty'+str(i),'task_family':'empty'+str(i),
                  'source':'deb','workflow':'new_project','split':'train'} for i in range(2)]
        counts={'c'+str(i):4 for i in range(8)}
        class ControlledStream:
            def __init__(self):self.calls=0
            def random(self):
                current=self.calls;self.calls+=1
                if current<2:return [0.1,0.9][current]
                draw,family=divmod(current-2,8)
                return 0.1 if draw in (3,5) and family<2 else 0.9
        stream=ControlledStream()
        with patch('propose_difficulty_split.random.Random',return_value=stream) as constructor:
            result=propose(rows,counts,seed=2026110801)
        constructor.assert_called_once_with(2026110801)
        self.assertEqual(stream.calls,2+10000*8)
        self.assertEqual(result['selected_draw_index_zero_based'],3)
        self.assertEqual(result['objective']['numerator'],0)
        self.assertEqual(result['assignments']['empty0'],'test')
        self.assertEqual(result['assignments']['empty1'],'train')
        self.assertEqual(result['candidate_split_counts'],{'test':2,'train':6})
