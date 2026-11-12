"""Audit native Qwen events against ATIF without replacing Harbor's converter."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import importlib.metadata
import inspect
import json
from pathlib import Path

from .io import digest, evidence_ok, evidence_ref, file_hash, read_json, read_jsonl

AUDITOR_VERSION = "qwen-atif-audit-v1"
CONVERTER_VERSION = "harbor-025-qwen-memory-preservation-v1"
ADAPTER_SHA256 = "434b6be964ab72ac7d1b95d60520bab48ef9af4bc9248d82a24809080b8e213d"


def conversion_identity():
    """A conversion version is independent of task/verifier execution identity."""
    from harbor.agents.installed.qwen_code import QwenCode
    if importlib.metadata.version('harbor') != '0.25.0':
        raise ValueError('Conversion requires Harbor 0.25.0')
    actual = file_hash(inspect.getfile(QwenCode))
    if actual != ADAPTER_SHA256:
        raise ValueError('Conversion adapter source changed; review a new converter version')
    return {'version':CONVERTER_VERSION, 'harbor_version':'0.25.0',
            'native_adapter_sha256':actual, 'implementation_sha256':file_hash(__file__)}


def _project_events(events):
    """Map one reviewed context event; classify metadata without discarding it."""
    projected, indices, metadata, gaps = [], [], [], []
    for index, original in enumerate(events):
        event = deepcopy(original)
        if not isinstance(event, dict) or event.get('type') != 'system':
            projected.append(event); indices.append(index)
            continue
        subtype = event.get('subtype')
        recognized = event.get('version') == '0.26.5'
        if subtype == 'memory_catalog_snapshot':
            message = event.get('message', {})
            parts = message.get('parts', []) if isinstance(message, dict) else []
            recognized = (recognized and isinstance(message,dict) and message.get('role') == 'user' and set(message) == {'role','parts'}
                and len(parts) == 1 and isinstance(parts[0],dict)
                and set(parts[0]) == {'text','partMetadata'} and isinstance(parts[0]['text'],str)
                and bool(parts[0]['text'])
                and parts[0]['partMetadata'] == {'qwen-code:memory-catalog-snapshot':True}
                and 'systemPayload' not in event)
            if recognized:
                # The pinned CLI inserts this as user-role context. It is not
                # a newly generated assistant message or an invented system prompt.
                event['type'] = 'user'
                event['message'] = {'role':'user','parts':[{'text':parts[0]['text']}]}
                projected.append(event); indices.append(index)
        elif subtype == 'ui_telemetry':
            payload = event.get('systemPayload', {})
            item = payload.get('uiEvent', {}) if isinstance(payload,dict) else {}
            recognized = (recognized and isinstance(payload,dict) and 'message' not in event and set(payload) == {'uiEvent'}
                and isinstance(item,dict) and item.get('event.name') in {
                    'request_lifecycle','qwen-code.api_response','qwen-code.tool_call'})
            if recognized:
                metadata.append({'event':index,'kind':subtype})
        elif subtype == 'attribution_snapshot':
            payload = event.get('systemPayload', {})
            snap = payload.get('snapshot', {}) if isinstance(payload,dict) else {}
            recognized = (recognized and isinstance(payload,dict) and 'message' not in event and set(payload) == {'snapshot'}
                and isinstance(snap,dict) and snap.get('type') == 'attribution-snapshot'
                and snap.get('version') == 1 and isinstance(snap.get('fileStates'),dict))
            if recognized:
                metadata.append({'event':index,'kind':subtype})
        else:
            recognized = False
        if not recognized:
            gaps.append(f'Unreviewed system event {index}: {subtype}; preserved in conversion envelope')
    return projected, indices, metadata, gaps


def derive_trajectory(events, native_atif):
    """Use the pinned native converter, retaining original ATIF and all raw events."""
    from harbor.agents.installed.qwen_code import QwenCode
    from harbor.models.trajectories import Trajectory
    identity = conversion_identity()
    native = Trajectory.model_validate(native_atif).model_dump(mode='json')
    agent = object.__new__(QwenCode)
    agent.model_name = native['agent']['model_name']
    kwargs = {'trajectory_id':native.get('trajectory_id'), 'agent_extra':native['agent'].get('extra')}
    reproduced = agent._convert_events_to_trajectory(events, **kwargs)
    if reproduced is None or reproduced.model_dump(mode='json') != native:
        raise ValueError('Retained native ATIF does not match the pinned converter and raw session')
    projected, indices, metadata, gaps = _project_events(events)
    converted = agent._convert_events_to_trajectory(projected, **kwargs)
    if converted is None:
        raise ValueError('No convertible conversation events')
    result = converted.model_dump(mode='json')
    result['extra'] = {'dbt_bench_conversion':{'identity':identity,
        'native_atif_sha256':digest(native), 'raw_events_sha256':digest(events),
        'raw_events':deepcopy(events), 'projected_event_indices':indices,
        'metadata_event_indices':metadata, 'unreviewed_events':gaps,
        'scope':'Native ATIF plus an explicit memory catalog context step; complete raw records retained as provenance. This is not an actual request reconstruction.'}}
    return Trajectory.model_validate(result).model_dump(mode='json')


def audit_derived_trace(events, native_atif, derived, *, context=None, root=Path.cwd()):
    """Recompute both correspondence and preservation; never trust a pass label."""
    native = harbor_schema_validator(native_atif).model_dump(mode='json')
    projected, indices, metadata, gaps = _project_events(events)
    envelope = (derived.get('extra') or {}).get('dbt_bench_conversion', {})
    if envelope.get('identity') != conversion_identity():
        gaps.append('Conversion identity changed')
    if envelope.get('raw_events') != events or envelope.get('raw_events_sha256') != digest(events):
        gaps.append('Conversion envelope lost or changed raw events')
    if envelope.get('native_atif_sha256') != digest(native):
        gaps.append('Conversion references another native ATIF')
    if envelope.get('projected_event_indices') != indices or envelope.get('metadata_event_indices') != metadata:
        gaps.append('Raw-to-converted event index mismatch')
    # Equality to a fresh native conversion also checks native metrics, timestamps,
    # agent version, memory step placement and every extra field, not just messages.
    if derived != derive_trajectory(events, native_atif):
        gaps.append('Derived trajectory differs from the declared converter output')
    quality = audit_trace(projected, derived, schema_validator=harbor_schema_validator, root=root)
    for mapping in quality['event_mapping']:
        mapping['event'] = indices[mapping['event']]
    calls = {part['functionCall']['id']: {'name':part['functionCall']['name']}
             for event in events if isinstance(event,dict) and event.get('type')=='assistant'
             for part in event.get('message',{}).get('parts',[]) if 'functionCall' in part}
    assistant_indices = [i for i,e in enumerate(events) if isinstance(e,dict) and e.get('type')=='assistant']
    status, context_gaps = _context_status(context, assistant_indices, calls, root)
    quality['model_context_capture'], quality['context_gaps'] = status, context_gaps
    quality['gaps'].extend(gaps)
    quality['content_fidelity'] = 'incomplete' if quality['gaps'] else 'complete'
    quality.update(converter_version=CONVERTER_VERSION, representation='derived_atif_with_raw_provenance',
                   raw_event_count=len(events), metadata_event_mapping=metadata)
    return quality


def audit_trace(events, atif, *, schema_validator=None, transformations=(), context=None, root=Path.cwd()):
    gaps, mapping = [], []
    if not events or not atif.get("steps"):
        gaps.append("Empty raw session or ATIF steps")
    schema_valid = None
    schema_error = None
    if schema_validator:
        try:
            schema_validator(atif)
            schema_valid = True
        except Exception as exc:
            schema_valid = False
            schema_error = str(exc)
    raw_calls, raw_results, messages, assistant_indices = {}, {}, [], []
    for i, event in enumerate(events):
        if not isinstance(event, dict):
            gaps.append(f"Unknown event {i}")
            continue
        typ = event.get("type")
        message = event.get("message")
        if typ not in {"user", "assistant", "tool_result"} or not isinstance(message, dict):
            gaps.append(f"Unrecognized event {i}: {typ}; retained in raw artifacts")
            continue
        if set(message) - {"role", "parts"}:
            gaps.append(f"Unmapped message fields at event {i}")
        if typ == "assistant":
            assistant_indices.append(i)
        texts, thoughts = [], []
        parts = message.get("parts")
        if not isinstance(parts, list):
            gaps.append(f"Unknown message shape at event {i}")
            continue
        for j, part in enumerate(parts):
            if not isinstance(part, dict) or not set(part) <= {"text", "thought", "functionCall", "functionResponse"}:
                gaps.append(f"Unrecognized part {i}:{j}")
                continue
            if "text" in part:
                if not isinstance(part["text"], str):
                    gaps.append(f"Nontext payload {i}:{j}")
                else:
                    (thoughts if part.get("thought") else texts).append(part["text"])
            if "functionCall" in part:
                call = part["functionCall"]
                cid = call.get("id")
                if not cid or cid in raw_calls:
                    gaps.append(f"Missing/duplicate raw call ID at {i}:{j}: {cid}")
                else:
                    raw_calls[cid] = {"name": call.get("name"), "args": call.get("args", {}), "event": i}
            if "functionResponse" in part:
                result = part["functionResponse"]
                cid, response = result.get("id"), result.get("response")
                if not cid or cid in raw_results or cid not in raw_calls:
                    gaps.append(f"Missing/duplicate/orphan raw result ID at {i}:{j}: {cid}")
                elif not isinstance(response, dict) or not set(response) <= {"output", "error"} or not response:
                    gaps.append(f"Unknown tool response at {i}:{j}")
                else:
                    raw_results[cid] = {"payload": response, "event": i}
        if typ in {"user", "assistant"}:
            messages.append({"source": "agent" if typ == "assistant" else "user",
                             "text": "\n".join(texts), "thought": "\n".join(thoughts), "event": i})
    atif_calls, atif_results, atif_messages = {}, {}, []
    for step in atif.get("steps", []):
        if step.get("source") in {"user", "agent"}:
            atif_messages.append({"source": step["source"], "text": step.get("message", ""),
                                  "thought": step.get("reasoning_content") or "", "step": step.get("step_id")})
        else:
            gaps.append(f"Unmapped ATIF step source: {step.get('source')}")
        for call in step.get("tool_calls") or []:
            cid = call.get("tool_call_id")
            if not cid or cid in atif_calls:
                gaps.append(f"Missing/duplicate ATIF call ID: {cid}")
            atif_calls[cid] = {"name": call.get("function_name"), "args": call.get("arguments", {}), "step": step.get("step_id")}
        for obs in (step.get("observation") or {}).get("results", []):
            cid = obs.get("source_call_id")
            if not cid or cid in atif_results:
                gaps.append(f"Missing/duplicate ATIF result ID: {cid}")
            atif_results[cid] = {"content": obs.get("content"), "step": step.get("step_id")}
    if len(messages) != len(atif_messages):
        gaps.append("Message count differs")
    for raw, converted in zip(messages, atif_messages):
        if any(raw[k] != converted[k] for k in ("source", "text", "thought")):
            gaps.append(f"Message/reasoning changed at raw event {raw['event']}")
        mapping.append({"event": raw["event"], "step": converted["step"], "kind": "message"})
    message_steps = {m["event"]: m["step"] for m in mapping}
    if set(raw_calls) != set(atif_calls):
        gaps.append("Tool call ID sets differ")
    if set(raw_results) != set(atif_results):
        gaps.append("Tool result ID sets differ")
    if set(raw_calls) != set(raw_results):
        gaps.append("Unanswered tool call(s); possible abnormal termination")
    for cid, raw in raw_calls.items():
        converted = atif_calls.get(cid, {})
        if any(raw[k] != converted.get(k) for k in ("name", "args")):
            gaps.append(f"Tool call payload changed: {cid}")
        if converted.get("step") != message_steps.get(raw["event"]):
            gaps.append(f"Tool call moved to wrong assistant event: {cid}")
    transformed = []
    for cid, raw in raw_results.items():
        converted = atif_results.get(cid, {})
        payload, content = raw["payload"], converted.get("content")
        # A structured response containing both output and error must retain both fields.
        expected = next(iter(payload.values())) if len(payload) == 1 else payload
        faithful = expected == content
        if not faithful:
            declaration = next((t for t in transformations if t.get("call_id") == cid
                                and t.get("raw_payload_hash") == digest(payload)
                                and t.get("atif_content_hash") == digest(content)
                                and t.get("reason")), None)
            if declaration:
                transformed.append(declaration)
            else:
                gaps.append(f"Tool result content lost/changed: {cid}")
        if converted.get("step") != atif_calls.get(cid, {}).get("step"):
            gaps.append(f"Tool result attached to wrong step: {cid}")
        mapping.append({"event": raw["event"], "step": converted.get("step"), "call_id": cid,
                        "raw_payload_hash": digest(payload)})
    context_status, context_gaps = _context_status(context, assistant_indices, raw_calls, root)
    return {"auditor_version": AUDITOR_VERSION, "schema_valid": schema_valid, "schema_error": schema_error,
            "content_fidelity": "incomplete" if gaps else "transformed" if transformed else "complete",
            "model_context_capture": context_status, "gaps": gaps, "context_gaps": context_gaps,
            "event_mapping": mapping, "declared_transformations": transformed,
            "raw_event_count": len(events), "tool_call_count": len(raw_calls)}


def _context_status(context, assistant_indices, raw_calls, root):
    if not context:
        return "unknown", ["No recording of actual model requests"]
    if context.get("kind") != "full_requests" or not evidence_ok(context.get("artifact"), root):
        return "incomplete", ["Missing/hash-mismatched full request capture"]
    requests = read_jsonl(Path(root) / context["artifact"]["path"])
    indices = [r.get("response_event_index") for r in requests]
    if Counter(indices) != Counter(assistant_indices):
        return "incomplete", ["Request/response event coverage differs"]
    names = {c["name"] for c in raw_calls.values()}
    for request in requests:
        body = request.get("request", {})
        messages = body.get("messages")
        tools = body.get("tools")
        if not isinstance(messages, list) or not any(m.get("role") == "system" for m in messages):
            return "incomplete", ["Actual system context is missing"]
        if not isinstance(tools, list) or not names <= {t.get("function", {}).get("name") for t in tools}:
            return "incomplete", ["Tool definitions missing; empty tools cannot reconstruct tool use"]
        if not request.get("request_id") or not request.get("capture_unmodified"):
            return "incomplete", ["Request provenance missing"]
    # Capture transport must itself have a reviewed conformance record.
    if not evidence_ok(context.get("capture_validation"), root):
        return "incomplete", ["Capture mechanism conformance evidence missing"]
    return "complete", []


def harbor_schema_validator(atif):
    from harbor.models.trajectories import Trajectory
    return Trajectory.model_validate(atif)


def inspect_trial_trace(trial_dir, *, root, context=None, conversion=None):
    trial_dir = Path(trial_dir)
    sessions = sorted(trial_dir.glob("agent/qwen-sessions/**/chats/*.jsonl"))
    atif_path = trial_dir / "agent/trajectory.json"
    if len(sessions) != 1 or not atif_path.exists():
        return {"schema_valid": None, "content_fidelity": "incomplete", "model_context_capture": "unknown",
                "gaps": [f"Expected one mapped session and one ATIF: {len(sessions)} sessions, ATIF exists={atif_path.exists()}"]}
    try:
        import harbor  # noqa: F401
        validator = harbor_schema_validator
    except ImportError:
        validator = None
    try:
        events, native_atif = read_jsonl(sessions[0]), read_json(atif_path)
        native = audit_trace(events, native_atif, schema_validator=validator, context=context, root=root)
        if conversion is None:
            return native
        if not evidence_ok(conversion, root):
            raise ValueError('Derived conversion manifest missing or changed')
        manifest = read_json(Path(root)/conversion['path'])
        expected = {'session':sessions[0], 'native_atif':atif_path, 'trial_result':trial_dir/'result.json'}
        for key, path in expected.items():
            if manifest.get(key) != evidence_ref(path,root):
                raise ValueError('Derived conversion belongs to a different '+key)
        if manifest.get('converter_identity') != conversion_identity():
            raise ValueError('Derived conversion implementation is stale')
        reference = manifest.get('derived_atif')
        if not evidence_ok(reference, root):
            raise ValueError('Derived ATIF missing or changed')
        derived = read_json(Path(root)/reference['path'])
        quality = audit_derived_trace(events,native_atif,derived,context=context,root=root)
        quality.update(native_quality=native, conversion_manifest=conversion, derived_artifact=reference)
        return quality
    except (ValueError, OSError, TypeError, KeyError, ImportError, AttributeError) as exc:
        return {"schema_valid": None, "content_fidelity": "incomplete", "model_context_capture": "unknown",
                "gaps": [f"Cannot parse retained trace: {exc}"]}
