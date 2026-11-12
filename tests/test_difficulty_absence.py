from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from difficulty_collect_v2 import collect
from difficulty_submission_v2 import verify_absences


class DifficultyAbsenceTest(unittest.TestCase):
    def test_absent_project_and_db_are_observed_not_capture_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            r = collect({'task_key': 'x', 'project_root': str(p/'missing-project'),
                         'database_paths': [str(p/'missing.db')]}, p/'out')
            self.assertTrue(r['inventory_complete'])
            self.assertTrue(r['complete'])
            self.assertEqual(len(r['absence_observations']), 3)
            self.assertEqual(r['project_files'], [])
            verify_absences(r)
            r['absence_observations'].pop()
            with self.assertRaises(ValueError):
                verify_absences(r)

    def test_permission_denied_is_never_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            with patch('difficulty_collect_v2.path_fact', side_effect=PermissionError('denied')):
                r = collect({'task_key': 'x', 'project_root': str(p/'project'),
                             'database_paths': [str(p/'db')]}, p/'out')
            self.assertFalse(r['complete'])
            self.assertEqual(r['absence_observations'], [])

    def test_empty_project_is_an_inventory_not_a_correct_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory); (p/'project').mkdir()
            r = collect({'task_key': 'x', 'project_root': str(p/'project'),
                         'database_paths': []}, p/'out')
            self.assertTrue(r['inventory_complete'])
            self.assertEqual(r['project_files'], [])
            self.assertNotIn('reward', r)
            verify_absences(r)

    def test_dangling_project_symlink_is_not_false_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory); (p/'project').symlink_to(p/'not-there')
            r = collect({'task_key': 'x', 'project_root': str(p/'project'),
                         'database_paths': []}, p/'out')
            self.assertFalse(r['complete'])
            self.assertEqual(r['absence_observations'], [])
