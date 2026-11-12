from pathlib import Path
import tempfile
import unittest
from dbt_bench import io
from difficulty_failure_qualification import validate, CONTROLS


class FailureQualificationTest(unittest.TestCase):
    def test_new_evaluator_requires_all_three_executed_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('old-checker','evaluator','overlay','control'):
                io.write_json(root/(name+'.json'), {'fixture':name})
            ref=lambda n:io.evidence_ref(root/(n+'.json'),root)
            qualification={'audit':{'overlay':ref('overlay')}}
            overlay={'code_refs':[ref('old-checker')]}
            io.write_json(root/'failure.json',{'rule_evidence':[ref('overlay')]})
            record={'evaluator':ref('evaluator'),'model_requests':0,'fixture_only':True,
                    'checks':{k:{'status':'pass','evidence':[ref('control')]} for k in CONTROLS}}
            io.write_json(root/'validation.json',record)
            receipt={'checker':ref('evaluator'),'qualified_checker':ref('old-checker'),
                     'failure_evaluation_ref':ref('failure'),'failure_evaluator_validation_ref':ref('validation')}
            validate(receipt,qualification,overlay,root)
            record['checks'].pop('healthy_submission_rejected_as_failure')
            io.write_json(root/'validation.json',record);receipt['failure_evaluator_validation_ref']=ref('validation')
            with self.assertRaises(ValueError):validate(receipt,qualification,overlay,root)

    def test_an_unqualified_checker_cannot_be_certified_by_a_new_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            receipt={'qualified_checker':{'path':'foreign.py','sha256':'x'}}
            with self.assertRaises(ValueError):
                validate(receipt,{'audit':{}},{'code_refs':[]},root)
