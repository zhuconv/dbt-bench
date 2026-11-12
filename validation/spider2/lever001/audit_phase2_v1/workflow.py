"""Verify a root-approved clean-rebuild evidence chain, not candidate log claims.

The caller supplies the trusted batch manifest and Harbor result references.
This module never treats an arbitrary candidate manifest/run_results as proof.
Source hashes bind what was actually executed; none is matched to a gold SQL.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

from audit_tables import CaptureError, load_observer_table
from dbt_bench.io import tree_hash
from audit import DATABASE, RAW_TABLES, RELATION, TASK, encode, read_hashed, ref


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _file_capture(folder, inventory):
    rows = {}
    for item in inventory:
        name = item['relative_path']
        if name in rows or Path(name).is_absolute() or '..' in Path(name).parts:
            raise CaptureError('Malformed trusted file inventory')
        if item.get('error'):
            raise CaptureError('Trusted file capture failed: ' + name)
        if item.get('exists'):
            path = folder / item['file']
            if ref(path)['sha256'] != item['sha256'] or path.stat().st_size != item['size']:
                raise CaptureError('Trusted file bytes changed: ' + name)
        rows[name] = item
    return rows


def check_trusted_rebuild(batch_ref, result_ref, raw_snapshot_ref, root):
    evidence = [batch_ref, result_ref, raw_snapshot_ref]
    try:
        root = Path(root)
        batch_path, batch = read_hashed(batch_ref, root)
        result_path, result = read_hashed(result_ref, root)
        _, raw = read_hashed(raw_snapshot_ref, root)
        if batch.get('kind') != 'audit_clean_reconstruction' or batch.get('model_requests') != 0:
            raise CaptureError('Not a root-approved clean reconstruction manifest')
        entry = batch['tasks'][result['task_name']]
        if entry['task_key'] != TASK or result.get('exception_info') is not None:
            raise CaptureError('Harbor task/exception mismatch')
        if result['config']['agent']['import_path'] != 'audit_rebuild_agent:AuditRebuildAgent' or result['config']['agent'].get('model_name') is not None:
            raise CaptureError('Wrong reconstruction adapter')
        task = Path(result['config']['task']['path'])
        if task.resolve() != (batch_path.parent/'tasks'/result['task_name']).resolve():
            raise CaptureError('Harbor task path differs from approved batch')
        if tree_hash(task) != entry['validation_copy_hash']:
            raise CaptureError('Prepared task/source/rebuild payload changed')
        for tool in batch['tools']:
            path = root/tool['path']
            if ref(path)['sha256'] != tool['sha256']:
                raise CaptureError('Frozen shared tool changed')
        request_path, request = read_hashed(entry['request'], root)
        evidence.append(entry['request'])
        if request['task_key'] != TASK or request['project_root'] != '/workspace':
            raise CaptureError('Wrong declared task/project')
        if request['commands'] != [['dbt', 'run', '--profiles-dir', '/workspace']]:
            raise CaptureError('Undeclared trusted build command')
        if request.get('fresh_artifact_paths', ['target','logs']) != ['target','logs']:
            raise CaptureError('Fresh artifact reset policy differs')
        if ref(root/entry['native_verifier']['path'])['sha256'] != entry['native_verifier']['sha256']:
            raise CaptureError('Original native verifier bytes changed')
        trial = result_path.parent
        ep = trial/'agent/audit-rebuild/execution.json'
        execution = json.loads(ep.read_text())
        evidence.append(ref(ep))
        driver = request_path.parent/'audit_rebuild_driver.py'
        if execution['driver_sha256'] != ref(driver)['sha256'] or execution['spec_sha256'] != entry['request']['sha256']:
            raise CaptureError('Trusted driver/request binding mismatch')
        evidence.append(ref(driver))
        shellp = trial/'agent/audit-rebuild-shell.json'
        shell = json.loads(shellp.read_text()); evidence.append(ref(shellp))
        no_modelp = trial/'agent/no-model.json'; no_model = json.loads(no_modelp.read_text()); evidence.append(ref(no_modelp))
        if no_model.get('model_requests') != 0 or no_model.get('solution_uploaded') is not False or execution.get('model_requests') != 0:
            raise CaptureError('Unexpected model/solution path')
        if shell['exit_code'] != 0 or 'audit_rebuild_driver.py --spec' not in shell['command'] or not shell.get('service_sha256'):
            raise CaptureError('Locked-shell execution receipt missing')
        if not _time(shell['started_at_utc']) <= _time(execution['started_at_utc']) <= _time(execution['finished_at_utc']) <= _time(shell['finished_at_utc']):
            raise CaptureError('Driver is outside its actual locked-shell time window')
        initial_hashes = {v['path']:v['sha256'] for v in execution['initial_input_hashes']}
        if initial_hashes.get(DATABASE) != raw['input_sha256']:
            raise CaptureError('Trusted rebuild started with a different original input')
        inventories = {}
        for field, folder in [('initial_files','initial-files'),('submitted_files','submitted-files'),('final_files','final-files')]:
            inventories[field] = _file_capture(ep.parent/folder, execution[field])
        for stage in ('initial_trees','final_trees'):
            for tree in execution[stage]:
                digest = hashlib.sha256(json.dumps(tree['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
                if digest != tree['content_sha256']:
                    raise CaptureError('Malformed dependency inventory')
        initial = execution['initial_trees'];final = execution['final_trees']
        checks = {'original_input_bound': True, 'frozen_packages_unchanged': initial==final and any(t['relative_path']=='dbt_packages' and t['exists'] for t in initial), 'trusted_build_success':execution.get('build_success') is True}
        run_results = []
        for index, run in enumerate(execution['runs']):
            if run['command'] != request['commands'][index] or run['cwd'] != '/workspace':
                raise CaptureError('Executed argv differs from approved command')
            log = ep.parent/run['log']
            if ref(log)['sha256'] != run['log_sha256']:
                raise CaptureError('Trusted command log changed')
            evidence.append(ref(log)); _file_capture(ep.parent/f'command-{index:02d}-files',run['files'])
            if run['returncode'] != 0:
                checks['trusted_build_success'] = False
        # A real failed build is a submission failure, not a fabricated schema.
        if not checks['trusted_build_success']:
            return {'status':'scored','reward':0.0,'checks':checks,'reason':'The actual trusted dbt command failed or did not complete.','evidence':evidence}
        manifestp = ep.parent/'final-files/target/manifest.json'; runp = ep.parent/'final-files/target/run_results.json'
        manifest = json.loads(manifestp.read_text()); runs = json.loads(runp.read_text()); evidence += [ref(manifestp),ref(runp)]
        run = execution['runs'][-1]
        for artifact in (manifest,runs):
            if not _time(run['started_at_utc']) <= _time(artifact['metadata']['generated_at']) <= _time(run['finished_at_utc']):
                raise CaptureError('Compiled artifact was not generated during trusted build')
        if manifest['metadata']['invocation_id'] != runs['metadata']['invocation_id']:
            raise CaptureError('Manifest/run-results invocation differs')
        model_nodes = {k:v for k,v in manifest['nodes'].items() if v.get('resource_type')=='model' and v.get('config',{}).get('enabled',True)}
        executable = {k for k,v in model_nodes.items() if v['config'].get('materialized')!='ephemeral'}
        successes = {v['unique_id'] for v in runs['results'] if v['status']=='success'}
        checks['all_enabled_models_built'] = executable <= successes and all(v['status']=='success' for v in runs['results'])
        target = [v for v in model_nodes.values() if v.get('alias')==RELATION and v.get('schema')=='main' and v.get('database')=='lever']
        checks['required_target_model_present'] = len(target)==1
        checks['required_target_built_this_invocation'] = len(target)==1 and target[0]['unique_id'] in successes
        source_consistent = all(inventories['submitted_files'][name].get('exists') == inventories['final_files'].get(name,{}).get('exists') and inventories['submitted_files'][name].get('sha256') == inventories['final_files'].get(name,{}).get('sha256') for name in inventories['submitted_files'] if not name.startswith(('target/','logs/')))
        for node in model_nodes.values():
            if node['package_name'] != manifest['metadata']['project_name']:
                continue
            name = node['original_file_path']
            before = inventories['submitted_files'].get(name);after = inventories['final_files'].get(name)
            if before is None or after is None:
                raise CaptureError('Enabled project model source was not captured: '+name)
            source_consistent &= before.get('exists') and after.get('exists') and before.get('sha256')==after.get('sha256')
            if after.get('exists'):
                source_consistent &= (ep.parent/'final-files'/name).read_text().strip()==node['raw_code'].strip()
        checks['final_source_matches_executed_model_graph'] = bool(source_consistent)
        initial_snap = ref(trial/'agent/audit-initial/snapshot.json'); submission_snap = ref(trial/'agent/audit-submission/snapshot.json')
        evidence += [initial_snap, submission_snap]
        unchanged = True
        for name in sorted(RAW_TABLES):
            left = load_observer_table(initial_snap,task_key=TASK,database=DATABASE,relation='main.'+name,stage='initial',root=root)
            right = load_observer_table(submission_snap,task_key=TASK,database=DATABASE,relation='main.'+name,stage='agent-submission',root=root)
            bag=lambda rows:Counter(json.dumps(row,sort_keys=True,default=encode,allow_nan=False) for row in rows)
            unchanged &= left.sql_types==right.sql_types and bag(left.rows)==bag(right.rows)
        checks['raw_sources_preserved'] = bool(unchanged)
        return {'status':'scored','reward':float(all(checks.values())),'checks':checks,'evidence':evidence,'invocation_id':runs['metadata']['invocation_id'],'successful_models':len(successes),'target_model_ids':[x['unique_id'] for x in target],'source_acceptance':'No equality with reference SQL bytes is required. Hashes bind captured/executed submission state; target discovery uses the fresh graph alias/schema.','scope':'Trusted clean reconstruction of this explicit submission only; not a retrospective claim about arbitrary historical candidate behavior.'}
    except (CaptureError,OSError,KeyError,ValueError,TypeError,AssertionError) as exc:
        return {'status':'unknown','reward':None,'reason':str(exc),'evidence':evidence}
