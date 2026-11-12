from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dbt_bench import io
from difficulty_interruption import classify, timestamp, missing_slot_status, scan_results
from propose_difficulty_split_v3 import summarize_task, draft
from tests.test_difficulty_split_v2 import attempt
from tests import test_difficulty_reporting as prior_reporting
from reconcile_difficulty_v6 import reconcile


def stop_at(value='2026-11-09T20:00:00Z'):
    return {'event': {'observed_at': timestamp(value), 'local_utc_offset_sec': -21600}, 'evidence': [], 'affects_missing_slots':True}


class InterruptionAccountingTest(unittest.TestCase):
    def test_completed_failure_and_native_budget_exhaustion_are_preserved(self):
        raw={'started_at':'2026-11-09T13:00:00','finished_at':'2026-11-09T13:59:00',
             'verifier_result':{'rewards':{'reward':0}},'exception_info':{'exception_type':'AgentTimeoutError'}}
        before=deepcopy(raw);result=classify(raw,stop_at(),last_observed=True)
        self.assertEqual(result['status'],'completed_before_external_stop')
        self.assertFalse(result['replacement_required']);self.assertEqual(raw,before)
        raw.pop('verifier_result')
        self.assertEqual(classify(raw,stop_at())['status'],'completed_before_external_stop')
        self.assertFalse(classify(raw,stop_at())['replacement_required'])

    def test_cutoff_is_chicago_local_not_utc_and_cannot_turn_into_model_failure(self):
        raw={'started_at':'2026-11-09T13:50:00','finished_at':'2026-11-09T14:00:05',
             'verifier_result':{'rewards':{'reward':0}}}
        result=classify(raw,stop_at())
        self.assertEqual(result['status'],'external_interrupted');self.assertTrue(result['replacement_required'])
        rows=[attempt(i,True,1)for i in range(1,5)]
        rows[3]['execution_adjudication']=result
        summary=summarize_task(rows)
        self.assertEqual(summary['valid_attempt_count'],3)
        self.assertFalse(summary['ready_for_draft']);self.assertEqual(summary['unresolved_execution_slots'],[4])

    def test_signal_during_cleanup_does_not_replace_fully_scored_behavior(self):
        raw={'started_at':'2026-11-09T13:50:00','finished_at':'2026-11-09T14:00:05',
             'agent_execution':{'finished_at':'2026-11-09T13:58:00'},
             'verifier':{'finished_at':'2026-11-09T13:59:59'},'verifier_result':{'rewards':{'reward':0}}}
        result=classify(raw,stop_at());self.assertEqual(result['status'],'completed_before_external_stop')
        self.assertFalse(result['replacement_required'])

    def test_legacy_last_trial_is_unresolved_not_guessed_zero_or_rerun(self):
        raw={'started_at':'2026-11-09T13:00:00','finished_at':'2026-11-09T13:59:00'}
        result=classify(raw,{'event':None,'evidence':[],'affects_missing_slots':True},last_observed=True)
        self.assertEqual(result['status'],'pending_boundary_review');self.assertFalse(result['replacement_required'])
        self.assertEqual(classify(raw,None)['status'],'no_external_stop_evidence')
        self.assertEqual(classify(raw,{'event':None,'evidence':[],'affects_missing_slots':False},last_observed=True)['status'],
                         'no_external_stop_evidence')

    def test_missing_terminal_json_is_retained_and_not_another_native_zero(self):
        with tempfile.TemporaryDirectory()as d:
            p=Path(d);io.write_json(p/'done/result.json',{'id':'a','started_at':'2026-11-09T13:00:00','config':{'task':'x'}})
            io.write_json(p/'interrupted/config.json',{'task':'x'});(p/'interrupted/result.json').write_text('{')
            records,incomplete=scan_results(p)
            self.assertEqual(len(records),1);self.assertEqual(len(incomplete),1)
            self.assertEqual(len(incomplete[0]['files']),2)
        self.assertEqual(missing_slot_status(stop_at(),has_unfinished_artifact=True),'external_interrupted_missing_result')
        self.assertEqual(missing_slot_status(stop_at(),has_unfinished_artifact=False),'not_started_due_to_external_stop')
        self.assertEqual(missing_slot_status(None,has_unfinished_artifact=False),'not_started')
        prior={**stop_at(),'affects_missing_slots':False}
        self.assertEqual(missing_slot_status(prior,has_unfinished_artifact=True),'incomplete_result_pending_review')

    def test_unstarted_member_reconciliation_emits_four_execution_slots_no_failures(self):
        with tempfile.TemporaryDirectory()as d:
            run=prior_reporting.DifficultyReportingTest().fixture(Path(d))
            with patch('reconcile_difficulty_v6.interruption_context',return_value=stop_at()):
                report=reconcile([run],[])
            task=report['tasks'][0]
            self.assertEqual(task['attempts'],[]);self.assertEqual(task['valid_attempt_count'],0)
            self.assertIsNone(task['joint_success_count']);self.assertEqual(len(report['recovery_slots']),4)
            self.assertTrue(all(r['action']=='run_unstarted_slot'for r in report['recovery_slots']))

    def test_incomplete_result_does_not_crash_whole_reconciliation(self):
        with tempfile.TemporaryDirectory()as d:
            run=prior_reporting.DifficultyReportingTest().fixture(Path(d));job=run/'jobs/run/partial'
            io.write_json(job/'config.json',{'task':'x'});(job/'result.json').write_text('{')
            with patch('reconcile_difficulty_v6.interruption_context',return_value=stop_at()):
                report=reconcile([run],[])
            self.assertEqual(report['actual_terminal_trials'],0)
            self.assertEqual(report['recovery_slots'][0]['action'],'replace_externally_interrupted_slot')
            self.assertEqual(len(report['recovery_slots']),4)
            self.assertTrue(report['runs'][0]['unfinished_artifacts'])

    def test_log_only_trial_is_not_silently_treated_as_unstarted(self):
        with tempfile.TemporaryDirectory()as d:
            p=Path(d);folder=p/'partial';folder.mkdir();(folder/'trial.log').write_text('actual partial log')
            results,incomplete=scan_results(p)
            self.assertEqual(results,[]);self.assertEqual(len(incomplete),1)
            self.assertEqual([p.name for p in incomplete[0]['files']],['trial.log'])

    def test_invalid_json_type_and_timestamp_are_retained_without_stopping_batch(self):
        with tempfile.TemporaryDirectory()as d:
            p=Path(d);io.write_json(p/'array/result.json',[])
            io.write_json(p/'badtime/result.json',{'id':'b','started_at':'not-a-time','config':{'task':'x'}})
            io.write_json(p/'valid/result.json',{'id':'v','started_at':'2026-11-09T13:00:00','config':{'task':'x'}})
            results,incomplete=scan_results(p)
            self.assertEqual([r[1]['id']for r in results],['v']);self.assertEqual(len(incomplete),2)

    def test_earlier_missing_result_does_not_renumber_later_trial(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);run=prior_reporting.DifficultyReportingTest().fixture(root)
            m=io.read_json(run/'manifest.json');config=m['identity']['config']
            agent={'name':'qwen-coder','model_name':'openai/test','base_url':'http://localhost/v1','kwargs':{}}
            config.update(agents=[agent],tasks=[{'path':str(root/'task')}])
            io.write_json(run/'config.yaml',config);m['config_hash']=io.file_hash(run/'config.yaml')
            m['experiment_id']=io.digest(m['identity']);io.write_json(run/'manifest.json',m)
            job=run/'jobs/run';io.write_json(job/'earlier/config.json',{'trial_name':'earlier'})
            io.write_json(job/'later/result.json',{'id':'later-actual-id','trial_name':'later',
                'started_at':'2026-11-09T13:00:00','finished_at':'2026-11-09T13:30:00',
                'config':{'agent':agent,'task':config['tasks'][0]},'verifier_result':{'rewards':{'reward':1}}})
            with patch('reconcile_difficulty_v6.interruption_context',return_value=stop_at()):
                report=reconcile([run],[])
            task=report['tasks'][0]
            self.assertEqual(task['attempts'],[])
            self.assertEqual(task['unbound_trials'][0]['trial_id'],'later-actual-id')
            self.assertIsNone(task['unbound_trials'][0]['attempt'])
            self.assertEqual(task['unbound_trials'][0]['native_rewards'],{'reward':1})
            self.assertTrue(task['slot_lineage_pending']);self.assertFalse(task['recovery_slots'])


class NoDrawPreflightTest(unittest.TestCase):
    def fixture(self,p,ready):
        proof=p/'proof.json';io.write_json(proof,{'fixture':True});ref=io.evidence_ref(proof,p);ref['path']=str(proof)
        catalog=p/'catalog.jsonl';io.write_jsonl(catalog,[{'task_key':'task','task_family':'f','source':'ade','workflow':'add_models','split':'train'}])
        catalog_ref=io.evidence_ref(catalog,p);catalog_ref['path']=str(catalog)
        rules=p/'rules.json';io.write_json(rules,{'family_definition':ref,'family_review':ref,'catalog':catalog_ref,
            'supersedes':ref,'excluded_categories':['broken'],'database_scope_note':'shared database',
            'random_seed':2026110801,'random_assignments':10000})
        pool=p/'pool.json';io.write_json(pool,{'phase4_review_complete':True,
            'tasks':[{'task_key':'task','task_version':'v1','eligible_for_measurement':True}]})
        rows=[attempt(i,True,1,evidence=[ref])for i in range(1,5)]if ready else []
        measurement=p/'measurement.json';io.write_json(measurement,{
            'candidate_pool':io.evidence_ref(pool,Path.cwd()),'split_rules':io.evidence_ref(rules,Path.cwd()),
            'tasks':[{'task_key':'task','task_version':'v1','attempts':rows}]})
        return pool,measurement,rules

    def test_ready_and_incomplete_dry_run_never_construct_rng_or_assign_families(self):
        for ready in (True,False):
            with self.subTest(ready=ready),tempfile.TemporaryDirectory()as d:
                p=Path(d);pool,measurement,rules=self.fixture(p,ready);out=p/'out'
                with patch('propose_difficulty_split_v3.random.Random',side_effect=AssertionError('RNG forbidden')), \
                     patch('propose_difficulty_split_v3.propose',side_effect=AssertionError('Draw forbidden')):
                    result=draft(pool,measurement,out,rules_path=rules,dry_run=True)
                self.assertEqual(result['would_allow_draw'],ready);self.assertEqual(result['random_draws_executed'],0)
                self.assertTrue((out/'preflight.json').exists());self.assertFalse((out/'proposal.json').exists())
                self.assertFalse((out/'family-assignments.jsonl').exists());self.assertNotIn('assignments',result)

    def test_interrupted_native_one_audit_one_still_blocks_draft(self):
        with tempfile.TemporaryDirectory()as d:
            p=Path(d);pool,measurement,rules=self.fixture(p,True);m=io.read_json(measurement)
            m['tasks'][0]['attempts'][-1]['execution_adjudication']={'status':'external_interrupted'}
            io.write_json(measurement,m)
            with patch('propose_difficulty_split_v3.propose',side_effect=AssertionError('Draw forbidden')):
                result=draft(pool,measurement,p/'out',rules_path=rules,dry_run=True)
            self.assertFalse(result['would_allow_draw']);self.assertEqual(result['blockers'][0]['unresolved_execution_slots'],[4])
