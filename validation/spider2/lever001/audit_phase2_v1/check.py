"""Supplemental Lever decision over approved, trusted Harbor captures.

No-build submissions are observed as submitted. Rebuilding a source submission
never retroactively certifies a different no-op actor or old native result.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
from audit_tables import CaptureError
from dbt_bench.io import tree_hash
import audit
import workflow


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _trusted_context(spec, root):
    bp, batch = audit.read_hashed(spec['batch_manifest'], root)
    rp, result = audit.read_hashed(spec['harbor_result'], root)
    if not rp.resolve().is_relative_to((bp.parent/'jobs').resolve()) or rp.parent.name != result['trial_name']:
        raise CaptureError('Result is not inside the approved batch trial tree')
    if batch.get('kind') != 'audit_clean_reconstruction' or batch.get('model_requests') != 0:
        raise CaptureError('Unapproved batch kind')
    if result.get('exception_info') is not None or not result.get('finished_at'):
        raise CaptureError('Harbor execution incomplete/exception')
    if result['config']['agent']['import_path'] != 'audit_rebuild_agent:AuditRebuildAgent' or result['config']['agent'].get('model_name') is not None:
        raise CaptureError('Untrusted agent type')
    entry = batch['tasks'][result['task_name']]
    task = Path(result['config']['task']['path'])
    if entry['task_key'] != audit.TASK or task.resolve() != (bp.parent/'tasks'/result['task_name']).resolve() or tree_hash(task) != entry['validation_copy_hash']:
        raise CaptureError('Prepared task/submission source binding differs')
    for reference in batch['tools']:
        if audit.ref(Path(root)/reference['path'])['sha256'] != reference['sha256']:
            raise CaptureError('Frozen collector tool changed')
    _, request = audit.read_hashed(entry['request'], root)
    return bp, batch, rp, result, request


def check_nop(spec, root):
    bp, batch, rp, result, request = _trusted_context(spec, root)
    if request.get('mode') != 'nop' or any(request['submission'].get(k) for k in ('files','reset_paths','delete_paths')):
        raise CaptureError('Not an approved unchanged no-build submission')
    trial = rp.parent
    no_modelp = trial/'agent/no-model.json'; receipt = json.loads(no_modelp.read_text())
    if receipt.get('mode') != 'nop' or receipt.get('model_requests') != 0 or receipt.get('solution_uploaded') is not False:
        raise CaptureError('Actual no-build adapter receipt missing')
    if (trial/'agent/audit-rebuild-shell.json').exists() or (trial/'agent/audit-rebuild/execution.json').exists():
        raise CaptureError('No-build trial unexpectedly executed reconstruction')
    _, raw = audit.read_hashed(spec['raw_snapshot'], root)
    evidence=[spec['batch_manifest'],spec['harbor_result'],spec['raw_snapshot'],audit.ref(no_modelp)]
    facts=[]
    for label,stage in [('audit-initial','initial'),('audit-submission','agent-submission')]:
        ip=trial/'agent'/(label+'-relations.json');sp=trial/'agent'/(label+'-relations-shell.json')
        inventory=json.loads(ip.read_text());shell=json.loads(sp.read_text());evidence += [audit.ref(ip),audit.ref(sp)]
        if inventory.get('task_key')!=audit.TASK or inventory.get('stage')!=stage:
            raise CaptureError('Wrong inventory task/stage')
        if shell.get('exit_code')!=0 or not shell.get('service_sha256') or 'audit_relation_inventory.py --spec' not in shell.get('command',''):
            raise CaptureError('Relation inventory was not captured by the trusted locked shell')
        if not _time(shell['started_at_utc']) <= _time(inventory['started_at_utc']) <= _time(inventory['finished_at_utc']) <= _time(shell['finished_at_utc']):
            raise CaptureError('Inventory outside actual shell invocation')
        target=[x for x in inventory['relations'] if x['database']==audit.DATABASE and x['relation']==audit.RELATION]
        if len(target)!=1:
            raise CaptureError('Required relation not uniquely declared')
        target=target[0]
        if target.get('error') or target.get('exists') not in (True,False) or target.get('input_unchanged') is not True:
            raise CaptureError('Relation existence was not established by a successful read-only query')
        if target['database_hashes_before']!=target['database_hashes_after'] or target['database_hashes_before'].get(audit.DATABASE)!=raw['input_sha256']:
            raise CaptureError('Original database/WAL changed or differs')
        if target['exists'] is False and target.get('matches')!=[]:
            raise CaptureError('Contradictory missing-relation inventory')
        facts.append({'stage':stage,'relation':audit.RELATION,'exists':target['exists'],'database_unchanged':True,'matches':target.get('matches')})
    if any(x['exists'] is not False for x in facts):
        return {'status':'unknown','reward':None,'reason':'This no-build branch only scores proved missing required output; no generic presence-to-workflow inference.','evidence':evidence,'facts':facts}
    return {'status':'scored','reward':0.0,'checks':{'required_output_exists':False,'no_build_mode_executed':True,'read_only_absence_proved':True},'reason':'The actual unchanged no-build submission lacks the required relation, as proved by information_schema in both initial and submission states. No build was run to repair or reinterpret it.','evidence':evidence,'facts':facts}


def run(spec, root):
    try:
        _,_,rp,result,request = _trusted_context(spec, root)
        if spec['mode']=='nop':
            decision=check_nop(spec,root)
            return {'schema_version':1,'task_key':audit.TASK,'mode':'nop','audit_status':decision['status'],'audit_reward':decision['reward'],'absence_audit':decision,'native_result':spec['harbor_result'],'original_native_reward':result.get('verifier_result'),'model_requests':0}
        if spec['mode']!='rebuild' or request.get('mode','rebuild')!='rebuild':
            raise CaptureError('Unsupported submission mode')
        wf=workflow.check_trusted_rebuild(spec['batch_manifest'],spec['harbor_result'],spec['raw_snapshot'],root)
        stages={}
        for where,stage in [('agent/audit-submission','agent-submission'),('verifier/mechanical-before','verifier-before'),('verifier/mechanical-after','verifier-after')]:
            stages[stage]=audit.replay({'raw_snapshot':spec['raw_snapshot'],'candidate_snapshot':audit.ref(rp.parent/where/'snapshot.json'),'candidate_stage':stage},root)
        values=[v['table_audit']['reward'] for v in stages.values()]+[wf['reward']]
        reward=0.0 if 0 in values else 1.0 if all(v==1 for v in values) else None
        return {'schema_version':1,'task_key':audit.TASK,'mode':'rebuild','audit_status':'scored' if reward is not None else 'unknown','audit_reward':reward,'workflow_audit':wf,'table_stages':stages,'native_result':spec['harbor_result'],'original_native_reward':result.get('verifier_result'),'model_requests':0}
    except (CaptureError,OSError,ValueError,KeyError,TypeError) as exc:
        return {'schema_version':1,'task_key':audit.TASK,'audit_status':'unknown','audit_reward':None,'reason':str(exc),'model_requests':0}


def main():
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--output',required=True);p.add_argument('--root',default='.');args=p.parse_args()
    spec=json.loads(Path(args.spec).read_text());result=run(spec,Path(args.root));result['checker']=audit.ref(__file__)
    Path(args.output).write_text(json.dumps(result,indent=2,default=audit.encode)+'\n');print(json.dumps({'status':result['audit_status'],'reward':result['audit_reward']}))

if __name__=='__main__':main()
