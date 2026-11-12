from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dbt_bench.io import digest, read_json, evidence_ref, write_json, write_jsonl
from dbt_bench.trace import audit_trace


def event(kind, parts):
    return {"type": kind, "message": {"parts": parts}}


def atif_for_error():
    return {"schema_version": "ATIF-v1.7", "session_id": "fixture", "agent": {"name": "qwen-code", "version": "0.26.5"}, "steps": [
        {"step_id": 1, "source": "user", "message": "Read the missing file."},
        {"step_id": 2, "source": "agent", "message": "", "tool_calls": [{"tool_call_id": "call-error", "function_name": "read_file", "arguments": {"path": "/missing.sql"}}],
         "observation": {"results": [{"source_call_id": "call-error", "content": "ENOENT: /missing.sql does not exist"}]}}]}


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.events = read_json(Path(__file__).with_name("error-only.json"))["events"]
        self.atif = atif_for_error()

    def test_error_only_text_and_id_preserved(self):
        result = audit_trace(self.events, self.atif)
        self.assertEqual(result["content_fidelity"], "complete")
        self.assertIsNone(result["schema_valid"])
        self.assertEqual(result["model_context_capture"], "unknown")
        self.assertEqual(result["event_mapping"][-1]["call_id"], "call-error")

    def test_native_output_only_loss_is_detected(self):
        self.atif["steps"][-1]["observation"]["results"][0]["content"] = ""
        result = audit_trace(self.events, self.atif)
        self.assertEqual(result["content_fidelity"], "incomplete")
        self.assertTrue(any("lost/changed" in g for g in result["gaps"]))

    def test_success_and_legal_empty_output(self):
        for output in ("done", ""):
            self.events[-1]["message"]["parts"][0]["functionResponse"]["response"] = {"output": output}
            self.atif["steps"][-1]["observation"]["results"][0]["content"] = output
            self.assertEqual(audit_trace(self.events, self.atif)["content_fidelity"], "complete")

    def test_parallel_ids_are_not_assigned_by_position(self):
        self.events[1]["message"]["parts"].append({"functionCall": {"id": "second", "name": "other", "args": {}}})
        self.events[-1]["message"]["parts"].insert(0, {"functionResponse": {"id": "second", "response": {"output": "other output"}}})
        step = self.atif["steps"][-1]
        step["tool_calls"].append({"tool_call_id": "second", "function_name": "other", "arguments": {}})
        step["observation"]["results"].append({"source_call_id": "second", "content": "other output"})
        self.assertEqual(audit_trace(self.events, self.atif)["content_fidelity"], "complete")
        step["observation"]["results"][0]["source_call_id"] = "second"
        self.assertEqual(audit_trace(self.events, self.atif)["content_fidelity"], "incomplete")

    def test_truncation_is_explicit_and_not_complete(self):
        payload = {"output": "z" * 100_000}
        self.events[-1]["message"]["parts"][0]["functionResponse"]["response"] = payload
        self.atif["steps"][-1]["observation"]["results"][0]["content"] = "z" * 10
        self.assertEqual(audit_trace(self.events, self.atif)["content_fidelity"], "incomplete")
        declaration = {"call_id": "call-error", "raw_payload_hash": digest(payload), "atif_content_hash": digest("z" * 10), "reason": "fixture truncation"}
        self.assertEqual(audit_trace(self.events, self.atif, transformations=[declaration])["content_fidelity"], "transformed")

    def test_retry_ids_and_compression_unknown_events(self):
        self.events.append(event("retry", []))
        self.events.append(event("compression", [{"text": "summary"}]))
        self.assertEqual(audit_trace(self.events, self.atif)["content_fidelity"], "incomplete")
        self.assertEqual(audit_trace(self.events, self.atif)["model_context_capture"], "unknown")

    def test_abnormal_termination_unanswered_call(self):
        self.events.pop()
        self.atif["steps"][-1].pop("observation")
        result = audit_trace(self.events, self.atif)
        self.assertEqual(result["content_fidelity"], "incomplete")
        self.assertTrue(any("Unanswered" in g for g in result["gaps"]))

    def test_message_reasoning_and_unknown_payload_not_dropped(self):
        self.events[1]["message"]["parts"].append({"text": "thinking", "thought": True})
        self.assertEqual(audit_trace(self.events, self.atif)["content_fidelity"], "incomplete")
        self.atif["steps"][-1]["reasoning_content"] = "thinking"
        self.assertEqual(audit_trace(self.events, self.atif)["content_fidelity"], "complete")
        self.events[1]["message"]["parts"].append({"inlineData": "unsupported"})
        self.assertEqual(audit_trace(self.events, self.atif)["content_fidelity"], "incomplete")

    def test_schema_failure_is_separate(self):
        def invalid(_):
            raise ValueError("fixture schema failure")
        result = audit_trace(self.events, self.atif, schema_validator=invalid)
        self.assertFalse(result["schema_valid"])
        self.assertEqual(result["content_fidelity"], "complete")

    def test_empty_session_not_complete(self):
        self.assertEqual(audit_trace([], {"steps": []})["content_fidelity"], "incomplete")

    def test_tool_call_step_misassociation_detected(self):
        self.events.insert(2, event("assistant", []))
        first = self.atif["steps"][-1]
        moved = {"step_id": 3, "source": "agent", "message": "", "tool_calls": first.pop("tool_calls"), "observation": first.pop("observation")}
        self.atif["steps"].append(moved)
        result = audit_trace(self.events, self.atif)
        self.assertEqual(result["content_fidelity"], "incomplete")
        self.assertTrue(any("wrong assistant" in g for g in result["gaps"]))

    def test_context_requires_real_tools_and_request_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requests = [{"request_id": "fixture-request", "response_event_index": 1, "capture_unmodified": True,
                         "request": {"messages": [{"role": "system", "content": "actual fixture system"}], "tools": []}}]
            write_jsonl(root / "requests.jsonl", requests)
            write_json(root / "capture-validation.json", {"scope": "synthetic fixture"})
            context = {"kind": "full_requests", "artifact": evidence_ref(root / "requests.jsonl", root),
                       "capture_validation": evidence_ref(root / "capture-validation.json", root)}
            self.assertEqual(audit_trace(self.events, self.atif, context=context, root=root)["model_context_capture"], "incomplete")
            requests[0]["request"]["tools"] = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
            write_jsonl(root / "requests.jsonl", requests)
            context["artifact"] = evidence_ref(root / "requests.jsonl", root)
            self.assertEqual(audit_trace(self.events, self.atif, context=context, root=root)["model_context_capture"], "complete")
            requests[0]["response_event_index"] = 999
            write_jsonl(root / "requests.jsonl", requests)
            context["artifact"] = evidence_ref(root / "requests.jsonl", root)
            self.assertEqual(audit_trace(self.events, self.atif, context=context, root=root)["model_context_capture"], "incomplete")
