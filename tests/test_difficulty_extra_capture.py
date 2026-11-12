from pathlib import Path
import tempfile
import unittest
from difficulty_collect_v3 import collect, sha
from difficulty_submission_v2 import verify_absences


class DifficultyAdditionalSourceTest(unittest.TestCase):
    def test_supplementary_source_kept_separate_without_certifying_equivalence(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory); other=p/'other'; other.mkdir()
            (other/'dbt_project.yml').write_text('name: actual')
            result=collect({'task_key':'x','project_root':str(p/'missing'), 'database_paths':[],
                'additional_project_roots':[{'path':str(other),'purpose':'readonly_path_equivalence_investigation',
                    'review':{'path':'synthetic-review','sha256':'fixture'}}]}, p/'out')
            self.assertTrue(result['complete']); self.assertEqual(result['project_files'],[])
            verify_absences(result)
            extra=result['additional_projects'][0]
            self.assertFalse(extra['approved_as_equivalent_delivery'])
            self.assertEqual(sha(p/'out'/extra['capture_file']),extra['capture_sha256'])

    def test_unreviewed_extra_root_cannot_silently_expand_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            with self.assertRaises(ValueError):
                collect({'task_key':'x','project_root':str(p/'a'),'database_paths':[],
                         'additional_project_roots':[{'path':str(p/'b')}]},p/'out')
            self.assertFalse((p/'out').exists())
