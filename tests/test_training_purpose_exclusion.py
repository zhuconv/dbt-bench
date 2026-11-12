from pathlib import Path
import unittest
from dbt_bench.report_v6 import eligibility, usage_scope


class MeasurementTrainingExclusionTest(unittest.TestCase):
    def trial(self):
        return {'split':'train','identity_verified':True,'environment_valid':True,'native_reward_valid':True,
                'native_success':True,'validation_current':True,'submission_verified':True,
                'audit_success':True,'same_submission_verified':True,
                'trace_quality':{'schema_valid':True,'content_fidelity':'complete','model_context_capture':'complete'}}

    def eligible(self,trial):
        return eligibility(trial,{'native':{'conclusion':'weak'},'audit':{'conclusion':'validated'}},
                           split_status='reviewed_frozen',root=Path.cwd())

    def test_even_a_perfect_train_success_cannot_export_measurement_traces(self):
        trial=self.trial();self.assertTrue(self.eligible(trial)['strict_sft_ready'])
        trial.update(usage_scope({'identity':{'purpose':'difficulty_only_no_training'}},{}))
        result=self.eligible(trial)
        self.assertFalse(result['candidate']);self.assertFalse(result['strict_sft_ready'])
        self.assertIn('experiment_prohibits_training',result['reasons'])

    def test_a_binding_cannot_remove_the_experiment_prohibition(self):
        for experiment in ({'identity':{'training_prohibited':True}},
                           {'training_prohibited':True,'identity':{}},
                           {'identity':{'purpose':'audit_conformance_only'}}):
            trial=self.trial();trial.update(usage_scope(experiment,{'training_prohibited':False}))
            self.assertFalse(self.eligible(trial)['candidate'])

    def test_trial_level_prohibition_is_preserved(self):
        trial=self.trial();trial.update(usage_scope({'identity':{}},{'training_prohibited':True}))
        self.assertFalse(self.eligible(trial)['candidate'])
