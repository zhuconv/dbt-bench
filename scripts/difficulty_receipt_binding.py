"""Cross-check actual audit contents, not just self-declared receipt labels."""
from pathlib import Path
from dbt_bench import io
from difficulty_failure import same_ref
from difficulty_submission_v2 import verify_capture


def read(ref, root):
    if not isinstance(ref, dict) or not io.evidence_ok(ref, root):
        raise ValueError('Missing or changed receipt dependency')
    return io.read_json(Path(root)/ref['path'])


def match(left, right, root, label):
    if not isinstance(left, dict) or not isinstance(right, dict) or not same_ref(left, right, root):
        raise ValueError('Receipt cross-binding differs: '+label)


def prepared_reference(binding, root):
    """Accept the two published spellings, never two conflicting identities."""
    first, second = binding.get('prepared_model_run'), binding.get('prepared_run')
    if first is not None and second is not None:
        match(first, second, root, 'prepared experiment aliases')
    reference = first if first is not None else second
    if not isinstance(reference, dict):
        raise ValueError('Source binding has no prepared experiment')
    return reference


def source_binding(receipt, root, *, allow_fixture=False):
    root=Path(root); binding=read(receipt['source_binding'], root)
    for name in ('task_key','task_version'):
        if binding.get(name)!=receipt[name]:raise ValueError('Source binding differs: '+name)
    for name in ('original_result','qualified_review'):
        match(binding.get(name),receipt[name],root,name)
    if binding.get('fixture_only') is not receipt['fixture_only']:
        raise ValueError('Fixture label differs inside source binding')
    fixture=receipt['fixture_only'] is True
    if fixture and not allow_fixture:
        raise ValueError('Fixture is never an actual model measurement')
    if not fixture and binding.get('actual_model_trial',True) is not True:
        raise ValueError('Source binding explicitly is not an actual model trial')
    prepared_ref=prepared_reference(binding,root)
    prepared=read(prepared_ref,root); identity=prepared['identity']
    if (prepared['experiment_id']!=io.digest(identity) or identity['task_key']!=receipt['task_key']
            or identity['task_version']!=receipt['task_version']):
        raise ValueError('Source binding refers to another prepared experiment')
    if not fixture and (identity['purpose']!='difficulty_only_no_training'
                         or prepared.get('fixture_only') is True or identity.get('fixture_only') is True):
        raise ValueError('Prepared experiment is not a model measurement')
    run=(root/prepared_ref['path']).parent
    config=io.read_json(run/'config.yaml')
    if config!=identity['config'] or io.file_hash(run/'config.yaml')!=prepared['config_hash']:
        raise ValueError('Prepared source configuration changed')
    task=Path(config['tasks'][0]['path'])
    if io.tree_hash(task)!=identity['actual_task_hash']:
        raise ValueError('Actual production task bytes changed')
    production=identity.get('reviewed_production') or identity['phase3_production']
    selected=production[receipt['task_key']]
    qualified=selected.get('qualified_review') or selected['pool_row']['phase3_record']
    match(qualified,receipt['qualified_review'],root,'prepared qualification')
    original=read(receipt['original_result'],root); trial=(root/receipt['original_result']['path']).parent
    if Path(original['config']['task']['path']).resolve()!=task.resolve():
        raise ValueError('Original trial ran a different production task')
    if not fixture:
        if original['config']['agent'].get('name')!='qwen-coder' or original['config']['agent'].get('import_path'):
            raise ValueError('Original execution is not the native model adapter')
        if trial.parent.resolve()!=(Path(config['jobs_dir'])/config['job_name']).resolve():
            raise ValueError('Original model trial is outside the prepared job')
    capture,_,files=verify_capture(trial,receipt['task_key'])
    captured=io.evidence_ref(trial/'artifacts/difficulty-submission/capture.json',root)
    extracted=binding.get('extracted')
    if extracted is not None:
        match(extracted.get('capture'),captured,root,'extracted capture')
        if extracted['task_key']!=receipt['task_key'] or extracted['project_root']!=capture['project_root']:
            raise ValueError('Extracted source belongs to another project')
        extracted_files=io.unique_index(extracted['source_files'],'path')
        if set(extracted_files)!=set(files):raise ValueError('Extracted source inventory is incomplete')
        for name, item in extracted_files.items():
            if any(item[field]!=files[name][field] for field in ('size','sha256','unchanged')):
                raise ValueError('Extracted source metadata changed')
            ref=item['captured_source']
            if ref['sha256']!=files[name]['sha256'] or not io.evidence_ok(ref,root):
                raise ValueError('Extracted source bytes differ from original capture')
    else:
        if receipt.get('evidence_kind')!='direct_contract_failure':
            raise ValueError('Ordinary scoring requires exact submitted source')
        match(binding.get('capture'),captured,root,'direct original capture')
    if receipt.get('original_capture'):
        match(receipt['original_capture'],captured,root,'receipt original capture')
    return {'binding':binding,'prepared':prepared,'trial':trial,'capture':capture,
            'capture_ref':captured,'fixture':fixture,'prepared_ref':prepared_ref}


def verify_rebuild(receipt,context,root):
    result=read(receipt['rebuild_result'],root)
    trial=(Path(root)/receipt['rebuild_result']['path']).parent
    batch=trial.parents[2]
    manifest=io.read_json(batch/'manifest.json')
    match(manifest.get('source_binding'),receipt['source_binding'],root,'rebuild source')
    if manifest.get('kind')!='difficulty_submission_reconstruction' or manifest.get('fixture_only') is not context['fixture']:
        raise ValueError('Reconstruction purpose differs from source binding')
    config=read(manifest['config'],root)
    if trial.parent.resolve()!=(Path(config['jobs_dir'])/config['job_name']).resolve():
        raise ValueError('Reconstruction result belongs to another job')
    task=Path(result['config']['task']['path'])
    entry=manifest['tasks'].get(task.name)
    if (entry is None or task.resolve()!=Path(config['tasks'][0]['path']).resolve()
            or entry['task_key']!=receipt['task_key'] or io.tree_hash(task)!=entry['validation_copy_hash']):
        raise ValueError('Reconstruction result belongs to another task')
    if (not result.get('finished_at') or result['config']['agent'].get('name')=='qwen-coder'):
        raise ValueError('Reconstruction is not a completed no-model audit')
    return trial


def verify_observed_snapshot(snapshot_ref,context,root):
    root=Path(root); path=root/snapshot_ref['path']; snapshot=read(snapshot_ref,root)
    if snapshot.get('task_key')!=context['binding']['task_key'] or snapshot.get('stage')!='agent-submission':
        raise ValueError('Checker snapshot task/stage differs')
    # DEB and Spider projections must originate from the original captured DB,
    # not the clean reconstruction or a different model attempt.
    process_path=path.parent.parent/'process.json'
    process=io.read_json(process_path)
    if process.get('returncode')!=0 or process.get('model_requests')!=0:
        raise ValueError('Original submission projection did not complete')
    expected={str((context['trial']/'artifacts/difficulty-submission'/r['file']).resolve()):r['sha256']
              for r in context['capture']['databases'] if r['exists']}
    actual={}
    for row in process['copies']:
        original=row['source']; copied=row['copy']; name=str((root/original['path']).resolve())
        if name in actual or original['sha256']!=copied['sha256']:
            raise ValueError('Projection copied duplicated or changed database bytes')
        if not io.evidence_ok(original,root) or not io.evidence_ok(copied,root):
            raise ValueError('Projection database evidence changed')
        actual[name]=original['sha256']
    if actual!=expected:raise ValueError('Projection used another submission or omitted database/WAL')
    probe=io.read_json(path.parent/'probe.json')
    if probe.get('database_and_wal_unchanged') is not True:
        raise ValueError('Projection modified submitted data')


def original_actions(reference, context, root):
    """A later reconstruction cannot stand in for original model tool events."""
    actions=read(reference,root)
    for ref in actions.get('source_logs',[]):
        path=(Path(root)/ref['path']).resolve()
        if not path.is_relative_to((context['trial']/'agent').resolve()) or not io.evidence_ok(ref,root):
            raise ValueError('Original action evidence belongs to another trial')


def delivery_basis(receipt,context,root,*,allow_fixture=False):
    reference=receipt.get('delivery_contract_ref')
    if reference is None:
        if allow_fixture:
            return 'actual_database'
        raise ValueError('DEB measurement has no reviewed delivery contract')
    contract=read(reference,root)
    for name in ('task_key','task_version'):
        if contract.get(name)!=receipt[name]:raise ValueError('Delivery contract differs: '+name)
    match(contract.get('qualified_review'),receipt['qualified_review'],root,'delivery qualification')
    task=Path(context['prepared']['identity']['config']['tasks'][0]['path'])
    # Review files may name another identical per-attempt production copy.
    for name,relative in (('instruction','instruction.md'),('native_build_path','tests/test_outputs.py')):
        ref=contract.get(name)
        if name=='native_build_path':
            alternative=contract.get('native_test')
            if ref is not None and alternative is not None:
                match(ref,alternative,root,'native delivery path aliases')
            ref=ref if ref is not None else alternative
        if not ref or not io.evidence_ok(ref,root) or ref['sha256']!=io.file_hash(task/relative):
            raise ValueError('Delivery contract is for different task contents')
    basis=contract.get('scoring_basis')
    if basis not in ('actual_database','submitted_source_reconstruction') or receipt.get('scoring_basis')!=basis:
        raise ValueError('Receipt contradicts its delivery contract')
    result=read(receipt['audit_result'],root)
    if result.get('delivery_contract_ref'):
        match(result['delivery_contract_ref'],reference,root,'checker delivery contract')
    if result.get('evaluated_snapshot'):
        match(result['evaluated_snapshot'],receipt.get('evaluated_snapshot'),root,'checker evaluated snapshot')
    return basis


def verify(receipt,root,*,allow_fixture=False):
    root=Path(root); context=source_binding(receipt,root,allow_fixture=allow_fixture)
    request=read(receipt['checker_request'],root)
    if request.get('task_key')!=receipt['task_key']:raise ValueError('Checker request has another task')
    if 'fixture_only' in request and request['fixture_only'] is not context['fixture']:
        raise ValueError('Checker request fixture label differs')
    if 'task_version' in request and request['task_version']!=receipt['task_version']:
        raise ValueError('Checker request has another task version')
    if request.get('qualified_review'):
        match(request['qualified_review'],receipt['qualified_review'],root,'request qualification')
    if request.get('original_events'):
        original_actions(request['original_events'],context,root)
    if receipt.get('evidence_kind')=='direct_contract_failure':
        if request.get('source_binding'):
            match(request['source_binding'],receipt['source_binding'],root,'failure request source')
        elif request.get('original_result') and request.get('capture'):
            match(request['original_result'],receipt['original_result'],root,'failure request result')
            match(request['capture'],context['capture_ref'],root,'failure request capture')
        else:
            raise ValueError('Direct request lacks original source/capture binding')
        return context
    rebuilt=verify_rebuild(receipt,context,root)
    if request.get('audit_dir') and Path(request['audit_dir']).resolve()!=(root/receipt['source_binding']['path']).parent.resolve():
        raise ValueError('Checker uses another reconstruction directory')
    if request.get('trial') and Path(request['trial']).resolve()!=rebuilt.resolve():
        raise ValueError('Checker uses another reconstruction trial')
    if request.get('batch') and Path(request['batch']).resolve()!=rebuilt.parents[2].resolve():
        raise ValueError('Checker uses another reconstruction batch')
    snapshot=request.get('snapshot') or request.get('candidate_snapshot')
    if not snapshot:raise ValueError('Checker request has no actual snapshot')
    if request.get('candidate_snapshot') and request.get('snapshot'):
        match(request['candidate_snapshot'],request['snapshot'],root,'checker snapshot aliases')
    expected=io.evidence_ref(rebuilt/'agent/audit-submission/snapshot.json',root)
    if receipt['task_key'].startswith('ade__'):
        match(snapshot,expected,root,'ADE source reconstruction output')
    elif receipt['task_key'].startswith('deb__'):
        basis=delivery_basis(receipt,context,root,allow_fixture=allow_fixture)
        observed=request.get('original_snapshot') or snapshot
        verify_observed_snapshot(observed,context,root)
        if request.get('reconstruction_snapshot'):
            match(request['reconstruction_snapshot'],expected,root,'DEB exact-source output')
        evaluated=receipt.get('evaluated_snapshot') or snapshot
        match(evaluated,expected if basis=='submitted_source_reconstruction' else observed,root,'DEB evaluated delivery')
        if request.get('probe'):
            match(request['probe'],io.evidence_ref((root/observed['path']).parent/'probe.json',root),root,'DEB original probe')
    else:
        verify_observed_snapshot(snapshot,context,root)
        if request.get('actual_probe'):
            match(request['actual_probe'],io.evidence_ref((root/snapshot['path']).parent/'probe.json',root),root,'Spider original probe')
    return context
