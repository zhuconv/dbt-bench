"""Consume the actual new runner's short mock-process signal journal downstream."""
from pathlib import Path
import unittest
from dbt_bench import io
from difficulty_interruption import context, classify, missing_slot_status
from tests import test_difficulty_gh_batch_v2 as runner_controls


@unittest.skipUnless(runner_controls.AVAILABLE, 'Locked runtime is required')
class RunnerJournalConsumerTest(unittest.TestCase):
    def test_generated_journal_binds_active_and_unstarted_members(self):
        fixture=runner_controls.BatchControls()
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        with self.assertRaises(runner_controls.runner.BatchInterrupted):fixture.execute(fail='signal')
        members=fixture.manifest['identity']['members']
        contexts=[]
        for member in members:
            run=Path(member['run']);identity=io.read_json(run/'manifest.json')['identity']
            contexts.append(context(run,identity,runner_controls.ROOT))
        self.assertTrue(all(c['affects_missing_slots']for c in contexts))
        self.assertEqual(contexts[0]['event']['active_member'],members[0]['run'])
        self.assertEqual(missing_slot_status(contexts[1],has_unfinished_artifact=False),'not_started_due_to_external_stop')
        retained=io.read_json(Path(members[0]['run'])/'launch-result.json')['retained_terminal_trials']
        self.assertEqual(len(retained),2)
        for row in retained:
            raw=io.read_json(runner_controls.ROOT/row['result']['path'])
            self.assertEqual(classify(raw,contexts[0])['status'],'completed_before_external_stop')
        self.assertEqual(missing_slot_status(contexts[0],has_unfinished_artifact=True),'external_interrupted_missing_result')
