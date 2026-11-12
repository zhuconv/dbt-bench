#!/usr/bin/env python3
"""Export a versioned derived ATIF; original session, ATIF and reward stay immutable."""
from pathlib import Path
import argparse
import json
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.io import evidence_ref,read_json,read_jsonl,write_json
from dbt_bench.trace import (audit_derived_trace, audit_trace, conversion_identity,
                             derive_trajectory, harbor_schema_validator)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial',type=Path,required=True)
    parser.add_argument('--context',type=Path,help='Optional reviewed context-binding JSON, never reconstructed prompts')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    sessions=list(args.trial.glob('agent/qwen-sessions/**/chats/*.jsonl'))
    if len(sessions)!=1:raise ValueError('Exactly one native main session required')
    native_path=args.trial/'agent/trajectory.json';result_path=args.trial/'result.json'
    before={key:evidence_ref(path,ROOT) for key,path in {
        'session':sessions[0],'native_atif':native_path,'trial_result':result_path}.items()}
    events,native=read_jsonl(sessions[0]),read_json(native_path)
    context=read_json(args.context) if args.context else None
    derived=derive_trajectory(events,native)
    write_json(out/'trajectory.json',derived)
    quality=audit_derived_trace(events,native,derived,context=context,root=ROOT)
    native_quality=audit_trace(events,native,schema_validator=harbor_schema_validator,context=context,root=ROOT)
    shutil.copyfile(__file__,out/'runner.py')
    manifest={'schema_version':1,'converter_identity':conversion_identity(), **before,
        'derived_atif':evidence_ref(out/'trajectory.json',ROOT),
        'runner':evidence_ref(out/'runner.py',ROOT), 'quality':quality,'native_quality':native_quality,
        'scope':'Offline export using the fixed native converter with explicit memory context mapping and complete raw event provenance. No task/model rerun, score change or actual request reconstruction.',
        'benchmark_trials':0,'model_requests':0}
    write_json(out/'manifest.json',manifest)
    for key,path in {'session':sessions[0],'native_atif':native_path,'trial_result':result_path}.items():
        if evidence_ref(path,ROOT)!=before[key]:raise ValueError('Original artifact changed: '+key)
    print(json.dumps({'schema_valid':quality['schema_valid'],'content_fidelity':quality['content_fidelity'],
        'model_context_capture':quality['model_context_capture'],'gaps':quality['gaps'],
        'manifest':str(out/'manifest.json')}))
    return 0 if quality['schema_valid'] is True and quality['content_fidelity']=='complete' else 1


if __name__=='__main__':
    raise SystemExit(main())
