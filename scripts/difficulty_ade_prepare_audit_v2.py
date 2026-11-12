"""Prepare exact delivered ADE source plus independent final failure probes."""
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from prepare_difficulty_audit_v2 import prepare as prepare_source
from difficulty_ade_prepare_clock_v2 import augment_audit as augment_clock
from difficulty_ade_registry import ENTRIES


def prepare(run,trial,output,fixture=False):
    output=Path(output)
    result=prepare_source(run,trial,output,fixture=fixture)
    if result['task_key']=='ade__ade-bench-f1009':augment_clock(output)
    batch=output/'batch';manifest=io.read_json(batch/'manifest.json')
    label,entry=next(iter(manifest['tasks'].items()));task=batch/'tasks'/label;payload=task/'audit-rebuild'
    path=payload/'request.json';request=io.read_json(path);requests=io.read_json(output/'requests.json')
    history=output/'failure-probe-preparation-before';history.mkdir()
    shutil.copyfile(batch/'manifest.json',history/'manifest.json');shutil.copyfile(path,history/'request.json')
    source=ROOT/'scripts/difficulty_ade_failure_probe_v2.py';index=len(request['capture_hooks']);name=f'capture-hook-{index:02d}.py'
    shutil.copyfile(source,payload/name)
    args=['--project',request['project_root'],'--database',ENTRIES[result['task_key']]['database'],
          '--target',request['project_root']+'/phase3_target','--output','{output}']
    request['capture_hooks'].append({'phase':'final','args':args,'payload_path':name,'sha256':io.file_hash(source)})
    requests['tasks'][0]['rebuild']['capture_hooks'].append({'phase':'final','args':args,'source':io.evidence_ref(source,ROOT)})
    io.write_json(path,request);io.write_json(output/'requests.json',requests)
    entry.update(request=io.evidence_ref(path,ROOT),validation_copy_hash=io.tree_hash(task))
    manifest.update(requests=io.evidence_ref(output/'requests.json',ROOT),ade_failure_probe={
        'source':io.evidence_ref(source,ROOT),'preparer':io.evidence_ref(Path(__file__),ROOT),
        'prior_unlaunched_preparation':io.evidence_ref(history/'manifest.json',ROOT)})
    io.write_json(batch/'manifest.json',manifest)
    return result
