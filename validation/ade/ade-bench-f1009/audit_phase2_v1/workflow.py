"""Task-specific trusted dbt reconstruction checks; no SQL implementation whitelist."""
from pathlib import Path, PurePosixPath
from datetime import datetime
import hashlib,json

def read_ref(ref, root):
    data = (root / ref['path']).read_bytes()
    if hashlib.sha256(data).hexdigest() != ref['sha256']:
        raise ValueError('Capture hash mismatch: ' + ref['path'])
    return data

def workflow_check(ref, snapshot_ref, task_key, root, model_name):
    """A successful captured build must bind the current source and dbt node.

    File hashes only bind observed artifacts to one invocation. They never
    choose which SQL implementation is accepted. Failed or missing invocation
    evidence is unknown; an observed absence of the requested model is wrong.
    """
    data = json.loads(read_ref(ref, root))
    if data.get('producer') not in ('task_image_selftest_clean_build', 'locked_qwen_clean_rebuild') or data.get('discarded_candidate_dbt_artifacts') is not True:
        raise ValueError('Workflow requires a trusted fresh build, not candidate dbt artifacts')
    if data['task_key'] != task_key or data['snapshot'] != snapshot_ref:
        raise ValueError('Workflow is not bound to this task and snapshot')
    project = json.loads(read_ref(data['project'], root))
    model_paths = project['model_paths']
    sources = data['sources']
    matches = []
    for item in sources:
        path = PurePosixPath(item['original_file_path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Invalid captured model path')
        if path.name == model_name + '.sql' and any(
                path.is_relative_to(PurePosixPath(p)) for p in model_paths):
            matches.append(item)
    if data.get('complete_model_inventory') is not True:
        raise ValueError('Model inventory was not captured completely')
    if not matches:
        return {'status': 'scored', 'reward': 0.0, 'checks': {'model_source': False},
                'reason': 'Complete configured-model-path inventory has no requested model source'}
    if len(matches) != 1:
        return {'status': 'scored', 'reward': 0.0, 'checks': {'unique_model_source': False},
                'reason': 'Multiple model sources have the required dbt model name'}
    source = matches[0]
    raw_code = read_ref(source['file'], root).decode()
    if not raw_code.strip():
        return {'status': 'scored', 'reward': 0.0, 'checks': {'nonempty_source': False},
                'reason': 'The requested model file is empty'}
    invocation = data['invocation']
    if invocation['returncode'] != 0:
        raise ValueError('Captured build did not succeed; inspect build failure separately')
    argv = invocation['argv']
    if not any(v in ('run', 'build') for v in argv):
        raise ValueError('No captured dbt materialization command')
    manifest = json.loads(read_ref(data['manifest'], root))
    results = json.loads(read_ref(data['run_results'], root))
    if manifest['metadata']['invocation_id'] != results['metadata']['invocation_id']:
        raise ValueError('dbt manifest/run_results invocation mismatch')
    for artifact in (manifest, results):
        generated = datetime.fromisoformat(artifact['metadata']['generated_at'].replace('Z', '+00:00')).timestamp()
        if not invocation['started_unix'] - 1 <= generated <= invocation['finished_unix'] + 1:
            raise ValueError('dbt artifact timestamp is outside the observed build invocation')
    nodes = [(key, node) for key, node in manifest['nodes'].items()
             if node.get('resource_type') == 'model' and node.get('name') == model_name]
    if len(nodes) != 1:
        raise ValueError('Requested model node not uniquely captured in manifest')
    key, node = nodes[0]
    # dbt's reader strips leading/trailing whitespace before storing raw_code.
    if node['original_file_path'] != source['original_file_path'] or node['raw_code'] != raw_code.strip():
        raise ValueError('Captured source differs from the dbt invocation source')
    runs = [v for v in results['results'] if v['unique_id'] == key]
    if len(runs) != 1 or runs[0]['status'] != 'success':
        raise ValueError('No successful materialization result for this model node')
    checks = {'model_source': True, 'source_bound_to_invocation': True,
              'successful_materialization': True,
              'relation_schema': node['schema'].casefold() == 'main',
              'relation_alias': node['alias'].casefold() == model_name,
              'materialization': node['config']['materialized'] in ('table', 'view', 'incremental')}
    # Incremental is legitimate if its observed current table passes all rows.
    return {'status': 'scored', 'reward': float(all(checks.values())), 'checks': checks,
            'reason': 'Actual captured dbt invocation binds current model source to materialized answer',
            'unique_id': key, 'invocation_id': manifest['metadata']['invocation_id']}
