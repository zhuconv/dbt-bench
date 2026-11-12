"""Reconstruct an existing CPU fixture with contracted build scope, no oracle retry."""
from pathlib import Path
import argparse
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from difficulty_deb_prepare_v4 import prepare
from difficulty_deb_conformance_v2 import command


def run(existing,output):
    existing,output=Path(existing),Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Fresh SCRATCH reconstruction required')
    basis=existing/'basis';m=io.read_json(basis/'manifest.json')
    if m.get('fixture_only') is not True or m['identity']['purpose']!='audit_conformance_only':
        raise ValueError('Only an existing explicit conformance fixture is supported')
    trials=list((existing/'oracle/jobs').glob('*/*/result.json'))
    if len(trials)!=1:raise ValueError('Expected one immutable original fixture capture')
    output.mkdir(parents=True)
    io.write_json(output/'plan.json',{'original_result':io.evidence_ref(trials[0],ROOT),
        'basis':io.evidence_ref(basis/'manifest.json',ROOT),'oracle_reruns':0,'model_requests':0,
        'fixture_only':True,'runner':io.evidence_ref(Path(__file__),ROOT)})
    prepare(basis,trials[0].parent,output/'rebuild',fixture=True)
    command([str(ROOT/'runtime/.venv/bin/harbor'),'run','-c',str(output/'rebuild/batch/config.yaml')],output,'rebuild')
    command([sys.executable,str(ROOT/'scripts/difficulty_deb_score_v8.py'),'--audit-dir',str(output/'rebuild'),
             '--output',str(output/'scoring')],output,'score')
    receipt=io.read_json(output/'scoring/receipt.json')
    outcome={'original_result':io.evidence_ref(trials[0],ROOT),'fixture_only':True,'model_requests':0,
        'oracle_reruns':0,'task_key':receipt['task_key'],'audit_reward':receipt['audit_reward'],
        'audit_status':receipt['audit_status'],'receipt':io.evidence_ref(output/'scoring/receipt.json',ROOT)}
    io.write_json(output/'outcome.json',outcome);return outcome


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--existing',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(run(a.existing,a.output))
