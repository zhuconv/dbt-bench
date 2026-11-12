from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from dbt_bench.trace import _project_events


class ConversionTests(unittest.TestCase):
    def memory(self):
        return {'type':'system','version':'0.26.5','subtype':'memory_catalog_snapshot',
                'message':{'role':'user','parts':[{'text':'exact memory',
                    'partMetadata':{'qwen-code:memory-catalog-snapshot':True}}]}}

    def test_memory_role_and_raw_index_are_preserved(self):
        events=[{'type':'user','message':{'parts':[{'text':'task'}]}},self.memory()]
        old=deepcopy(events)
        projected,indices,metadata,gaps=_project_events(events)
        self.assertEqual(events,old)
        self.assertEqual(projected[1]['type'],'user')
        self.assertEqual(projected[1]['message'],{'role':'user','parts':[{'text':'exact memory'}]})
        self.assertEqual(indices,[0,1]);self.assertFalse(metadata);self.assertFalse(gaps)

    def test_unknown_or_malformed_metadata_cannot_be_certified(self):
        for bad in [None,[],7,{'parts':[]},{'role':'system','parts':[{'text':'not a memory catalog'}]}]:
            event=self.memory();event['message']=bad
            projected,_,_,gaps=_project_events([event])
            self.assertFalse(projected);self.assertTrue(gaps)
        for kind in ['chat_compression','agent_retry','new_future_event']:
            event=self.memory();event['subtype']=kind
            self.assertTrue(_project_events([event])[-1])

    def test_telemetry_with_new_conversation_content_is_not_ignored(self):
        event={'type':'system','version':'0.26.5','subtype':'ui_telemetry',
               'systemPayload':{'uiEvent':{'event.name':'request_lifecycle'}},
               'message':{'role':'user','parts':[{'text':'new content'}]}}
        self.assertTrue(_project_events([event])[-1])
        event.pop('message')
        _,_,metadata,gaps=_project_events([event])
        self.assertFalse(gaps);self.assertEqual(metadata,[{'event':0,'kind':'ui_telemetry'}])

    def test_native_converter_conformance(self):
        root=Path(__file__).resolve().parents[2]
        python=root/'runtime/.venv/bin/python'
        if not python.exists():self.skipTest('Pinned Harbor runtime required for actual converter integration')
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'conformance'
            process=subprocess.run([str(python),str(root/'scripts/check_trace_conversion.py'),
                                    '--output-dir',str(out)],capture_output=True,text=True,timeout=60)
            self.assertEqual(process.returncode,0,process.stderr+process.stdout)
            summary=json.loads((out/'summary.json').read_text())
            self.assertTrue(summary['all_checks_passed'])
            self.assertGreaterEqual(len(summary['cases']),18)
