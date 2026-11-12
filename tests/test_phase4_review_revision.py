from copy import deepcopy
import unittest

from revise_phase4_review import bind_revision


class Phase4ReviewRevisionTest(unittest.TestCase):
    def fixture(self):
        old = {'task_key': 'deb__example', 'task_version': 'v1',
               'phase4_identity': {'instruction': 'fixed', 'image': 'fixed'},
               'native': {'reward': 1, 'conclusion': 'weak', 'overlay': {'path': 'native-original', 'sha256': 'n'}},
               'reproduction': {'two_trials': ['a', 'b']},
               'overlays': {'native': {'path': 'native-copy', 'sha256': 'n'}, 'audit': {'path': 'audit-v1', 'sha256': 'a'}},
               'audit': {'checker_version': 'v1', 'conclusion': 'pending'}}
        new = deepcopy(old)
        new['overlays']['audit'] = {'path': 'audit-v2', 'sha256': 'b'}
        new['audit'] = {'checker_version': 'v2', 'conclusion': 'validated'}
        return old, new

    def test_revision_preserves_old_native_and_does_not_count_a_new_task(self):
        old, new = self.fixture(); saved_old = deepcopy(old)
        # Identical native bytes may be rechecked through their archived path.
        new['native']['overlay']['path'] = 'native-copy'
        ref = {'path': 'prior-companion', 'sha256': 'prior'}
        result = bind_revision(old, new, ref, 'Actual regressions established lawful column representations')
        self.assertEqual(old, saved_old)
        self.assertEqual(result['native'], old['native'])
        self.assertEqual(result['previous_companion'], ref)
        self.assertEqual(result['amendment']['new_completed_task_credit'], 0)
        self.assertEqual(result['audit']['conclusion'], 'validated')

    def test_audit_revision_cannot_smuggle_a_task_or_native_change(self):
        for field, value in [('task_version', 'new-prompt'), ('phase4_identity', {'image': 'different'}),
                             ('native', {'reward': 0}), ('reproduction', {'two_trials': ['c', 'd']})]:
            old, new = self.fixture(); new[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                bind_revision(old, new, {'path': 'prior', 'sha256': 'prior'}, 'Revision')
