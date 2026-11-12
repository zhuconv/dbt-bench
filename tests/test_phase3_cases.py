"""Actual case-hook ordering and independent-validity gates before native scoring."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from phase3_prepare_cases import proof_gate_source


class Phase3CaseTests(unittest.TestCase):
    def gate(self, kind, proof):
        class Proof:
            def exists(self): return proof is not None
            def read_text(self): return json.dumps(proof)
        with patch('pathlib.Path', return_value=Proof()):
            exec(proof_gate_source(kind), {})

    def test_invalid_or_circular_negative_never_reaches_scoring(self):
        for proof in (None, {}, {'validity': 'sql_error'}, {'validity': 'business_error'},
                      {'validity': 'business_error', 'tested_checker_invoked_to_define_validity': True},
                      {'validity': 'business_error', 'tested_checker_invoked_to_define_validity': 0}):
            with self.subTest(proof=proof), self.assertRaises(AssertionError):
                self.gate('negative', proof)
        self.gate('negative', {'validity': 'business_error', 'tested_checker_invoked_to_define_validity': False})

    def test_equivalence_proof_cannot_be_used_as_wrong_answer(self):
        proof = {'validity': 'legal_equivalent', 'tested_checker_invoked_to_define_validity': False}
        self.gate('equivalent', proof)
        with self.assertRaises(AssertionError): self.gate('negative', proof)

    def test_case_runs_after_build_before_submission_capture(self):
        calls = []
        class Parent:
            async def shell(self, environment, name, command, pty=False, timeout=300):
                calls.append(('shell', name, command)); return name
        class Environment:
            async def upload_dir(self, source, target): calls.append(('upload', str(source), target))
        fake = types.ModuleType('audit_rebuild_agent'); fake.AuditRebuildAgent = Parent
        path = Path(__file__).resolve().parents[1] / 'scripts/phase3_case_agent.py'
        spec = importlib.util.spec_from_file_location('test_case_adapter', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'audit_rebuild_agent': fake}): spec.loader.exec_module(module)
        agent = module.Phase3CaseAgent()
        agent._task = types.SimpleNamespace(paths=types.SimpleNamespace(task_dir=Path('/synthetic/task')))
        env = Environment()
        asyncio.run(agent.shell(env, 'audit-initial', 'observe initial'))
        asyncio.run(agent.shell(env, 'audit-rebuild', 'actual build'))
        self.assertEqual([c[1] for c in calls], ['audit-initial', 'audit-rebuild'])
        asyncio.run(agent.shell(env, 'audit-submission', 'observe submission'))
        self.assertEqual([c[0] for c in calls], ['shell', 'shell', 'upload', 'shell', 'shell'])
        self.assertEqual(calls[-2][1], 'phase3-case')
        self.assertIn('build_gate.py && python3 ', calls[-2][2])
        self.assertEqual(calls[-1][1:], ('audit-submission', 'observe submission'))


if __name__ == '__main__': unittest.main()
