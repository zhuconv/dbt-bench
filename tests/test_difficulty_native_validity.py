import importlib.util
from pathlib import Path
import unittest
from difficulty_native_validity import classify_native


@unittest.skipUnless(importlib.util.find_spec('harbor'), 'Requires the locked Harbor runtime')
class DifficultyNativeValidityTest(unittest.TestCase):
    def raw(self, exception=None, reward=0):
        return {'verifier_result': {'rewards': {'reward': reward}},
                'exception_info': {'exception_type': exception} if exception else None}

    def test_task_timeout_with_a_real_score_is_measurable(self):
        r = classify_native(self.raw('AgentTimeoutError'), Path.cwd())
        self.assertTrue(r['reward_valid'])
        self.assertEqual(r['exceptions'][0]['failure_cause'], 'agent_exhausted_budget')
        self.assertFalse(r['exceptions'][0]['automatic_retry_performed'])

    def test_external_timeout_and_unknown_exit_remain_invalid(self):
        for kind in ('ApiConnectionError', 'CancelledError', 'NonZeroAgentExitCodeError', 'UnknownError'):
            with self.subTest(kind=kind):
                self.assertFalse(classify_native(self.raw(kind), Path.cwd())['reward_valid'])

    def test_missing_reward_cannot_be_manufactured_from_timeout(self):
        raw = self.raw('AgentTimeoutError'); raw['verifier_result'] = None
        self.assertFalse(classify_native(raw, Path.cwd())['reward_valid'])

    def test_step_exception_is_preserved(self):
        raw = self.raw(); raw['step_results'] = [{'exception_info': {'exception_type': 'VerifierTimeoutError'}}]
        self.assertFalse(classify_native(raw, Path.cwd())['reward_valid'])
