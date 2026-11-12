#!/usr/bin/env python3
"""Execute conversion preservation and rejection fixtures with pinned Harbor."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.io import evidence_ref,read_json,write_json,write_jsonl
from dbt_bench.trace import (audit_derived_trace, audit_trace, conversion_identity,
                             derive_trajectory, harbor_schema_validator, inspect_trial_trace)


def fixtures():
    base=read_json(ROOT/'tests/trace_fidelity/error-only.json')['events']
    memory={'type':'system','version':'0.26.5','subtype':'memory_catalog_snapshot',
        'uuid':'memory-fixture','message':{'role':'user','parts':[{'text':'Current memory catalog: fixture exact text',
            'partMetadata':{'qwen-code:memory-catalog-snapshot':True}}]}}
    metadata=[{'type':'system','version':'0.26.5','subtype':'ui_telemetry','systemPayload':{'uiEvent':{'event.name':name}}}
              for name in ['request_lifecycle','qwen-code.api_response','qwen-code.tool_call']]
    metadata.append({'type':'system','version':'0.26.5','subtype':'attribution_snapshot',
                     'systemPayload':{'snapshot':{'type':'attribution-snapshot','version':1,'fileStates':{'fixture':'exact preserved payload'}}}})
    cases=[]
    def add(name,events,complete=True,tamper=None):
        cases.append({'name':name,'events':events,'expected':'complete' if complete else 'incomplete','tamper':tamper})
    full=[deepcopy(base[0]),deepcopy(memory),*deepcopy(metadata),*deepcopy(base[1:])]
    add('memory_metadata_error_only',full)
    add('memory_multiple_snapshots',[deepcopy(memory),*deepcopy(base),deepcopy(memory)])
    for name,output in [('empty_output',''),('tool_success','done'),('long_output','z'*120000)]:
        events=deepcopy(full);events[-1]['message']['parts'][0]['functionResponse']['response']={'output':output}
        add(name,events)
    events=deepcopy(full)
    events[-2]['message']['parts'].append({'functionCall':{'id':'parallel','name':'other','args':{'x':2}}})
    events[-1]['message']['parts'].insert(0,{'functionResponse':{'id':'parallel','response':{'output':'parallel result'}}})
    add('parallel_reversed_results',events)
    for subtype in ['chat_compression','agent_retry','unknown_future_context']:
        add(subtype,[*deepcopy(full),{'type':'system','version':'0.26.5','subtype':subtype,'message':{'parts':[{'text':'must not disappear'}]}}],False)
    events=deepcopy(full);events[1]['message']['parts'][0]['partMetadata']['qwen-code:memory-catalog-snapshot']=False
    add('malformed_memory',events,False)
    events=deepcopy(full);events[2]['systemPayload']['uiEvent']['event.name']='unreviewed_event'
    add('unknown_telemetry',events,False)
    events=deepcopy(full);events[-1]['message']['parts'][0]['functionResponse']['response']['output']='partial output'
    add('combined_output_error',events,False)
    add('abnormal_unanswered_call',deepcopy(full[:-1]),False)
    add('tampered_observation',deepcopy(full),False,'observation')
    add('tampered_raw_metadata',deepcopy(full),False,'raw')
    add('tampered_metrics',deepcopy(full),False,'metrics')
    add('stale_converter_identity',deepcopy(full),False,'identity')
    add('memory_step_deleted',deepcopy(full),False,'memory')
    return cases


def run(output):
    from harbor.agents.installed.qwen_code import QwenCode
    identity=conversion_identity()
    output.mkdir(parents=True,exist_ok=False)
    agent=object.__new__(QwenCode);agent.model_name='openai/fixture'
    rows=[]
    for case in fixtures():
        native=agent._convert_events_to_trajectory(case['events']).model_dump(mode='json')
        original=deepcopy(native)
        derived=derive_trajectory(case['events'],native)
        assert native==original
        if case['tamper']=='observation':
            derived['steps'][-1]['observation']['results'][0]['content']=''
        elif case['tamper']=='raw':
            derived['extra']['dbt_bench_conversion']['raw_events'][2]['systemPayload']['uiEvent']['event.name']='changed'
        elif case['tamper']=='metrics':
            derived['final_metrics']['total_steps']+=1
        elif case['tamper']=='identity':
            derived['extra']['dbt_bench_conversion']['identity']['implementation_sha256']='changed'
        elif case['tamper']=='memory':
            derived['steps'].pop(1)
        audit=audit_derived_trace(case['events'],native,derived,root=ROOT)
        observed=audit['content_fidelity']
        passed=observed==case['expected']
        if case['expected']=='complete':
            passed &= audit['schema_valid'] is True
            assert derived['extra']['dbt_bench_conversion']['raw_events']==case['events']
        if case['name']=='memory_metadata_error_only':
            assert audit_trace(case['events'],native)['content_fidelity']=='incomplete'
            assert derived['steps'][1]['source']=='user'
            assert derived['steps'][1]['message']=='Current memory catalog: fixture exact text'
        row={'name':case['name'],'passed':bool(passed),'expected':case['expected'],'actual':observed}
        evidence=output/(case['name']+'.json')
        write_json(evidence,{'case':case,'native_atif':native,'derived_atif':derived,'audit':audit})
        rows.append({**row,'evidence':evidence_ref(evidence,ROOT)})
    # A companion cannot turn tampered content into a passing trace by claiming
    # success. The report path must independently read and recompute its evidence.
    trial=output/'reader-trial';session=trial/'agent/qwen-sessions/fixture/chats/session.jsonl'
    events=fixtures()[0]['events']
    native=agent._convert_events_to_trajectory(events).model_dump(mode='json')
    write_jsonl(session,events);write_json(trial/'agent/trajectory.json',native)
    write_json(trial/'result.json',{'fixture_only':True})
    derived=derive_trajectory(events,native);write_json(output/'reader-derived.json',derived)
    manifest={'converter_identity':identity, 'session':evidence_ref(session,ROOT),
        'native_atif':evidence_ref(trial/'agent/trajectory.json',ROOT),
        'trial_result':evidence_ref(trial/'result.json',ROOT),
        'derived_atif':evidence_ref(output/'reader-derived.json',ROOT),
        'quality':{'content_fidelity':'complete'}}
    reader=[]
    for name in ['valid','forged_pass','stale_identity','wrong_trial']:
        changed=deepcopy(manifest)
        if name=='forged_pass':
            altered=deepcopy(derived)
            altered['steps'][-1]['observation']['results'][0]['content']='lost error'
            write_json(output/'reader-forged.json',altered)
            changed['derived_atif']=evidence_ref(output/'reader-forged.json',ROOT)
        elif name=='stale_identity':
            changed['converter_identity']['implementation_sha256']='changed'
        elif name=='wrong_trial':
            changed['session']=evidence_ref(trial/'result.json',ROOT)
        path=output/('reader-'+name+'.json');write_json(path,changed)
        audit=inspect_trial_trace(trial,root=ROOT,conversion=evidence_ref(path,ROOT))
        expected='complete' if name=='valid' else 'incomplete'
        reader.append({'name':name,'expected':expected,'actual':audit['content_fidelity'],
                       'passed':audit['content_fidelity']==expected})
    result={'schema_version':1,'converter_identity':identity,'benchmark_trials':0,'model_requests':0,
        'kind':'synthetic_events_through_actual_native_converter','cases':rows,
        'reader_checks':reader,
        'all_checks_passed':all(r['passed'] for r in rows+reader),
        'scope':'Explicit memory mapping, raw-event preservation and rejection of known gaps. Does not certify unreviewed retries/compression or actual request capture.'}
    write_json(output/'summary.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();result=run(args.output_dir)
    print(json.dumps({'passed':sum(r['passed'] for r in result['cases']),'cases':len(result['cases']),
                      'all_checks_passed':result['all_checks_passed']}))
    raise SystemExit(0 if result['all_checks_passed'] else 1)
