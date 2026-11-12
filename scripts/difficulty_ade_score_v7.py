#!/usr/bin/env python3
"""Apply qualified ADE source checkers to captured model-source rebuilds (v2).

The fresh build supplies trusted dbt artifacts. Its commands are never evidence
that the model itself ran those commands. This adapter explicitly lists the reviewed contracts that accept delivered
dbt source; contracts requiring model-issued commands need a different adapter.
"""
import argparse
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import tomllib

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from difficulty_submission import verify_capture
from prepare_difficulty_audit_v2 import source_plan

from difficulty_ade_registry import ENTRIES, contract_record
from difficulty_ade_inputs import validate_initial
from difficulty_ade_session import review as review_session

REGISTRY={k:(v["policy"],v["checker"],v["database"])for k,v in ENTRIES.items()}


def load(ref):
    if not io.evidence_ok(ref, ROOT):
        raise ValueError('Evidence changed: ' + ref['path'])
    return io.read_json(ROOT / ref['path'])


def ref(path):
    return io.evidence_ref(path, ROOT)


def save(path, value):
    if path.exists():
        raise ValueError('Refuse to overwrite audit evidence: ' + str(path))
    io.write_json(path, value)
    return ref(path)


def inventory_map(execution, field):
    return io.unique_index(execution[field], 'relative_path')


def assert_delivered_sources(execution, source_names, extracted):
    """All reconstructed source bytes must be the actual delivered bytes."""
    captured = io.unique_index(extracted['source_files'], 'path')
    submitted = inventory_map(execution, 'submitted_files')
    final = inventory_map(execution, 'final_files')
    for name in source_names:
        expected = captured[name]['sha256']
        for rows in (submitted, final):
            row = rows.get(name)
            if not row or not row.get('exists') or row.get('error') or row['sha256'] != expected:
                raise ValueError('Clean build did not preserve delivered source: ' + name)


def score(audit_dir, output):
    audit_dir, output = Path(audit_dir), Path(output)
    if output.exists():
        raise ValueError('Use a new output directory')
    batch = audit_dir / 'batch'
    manifest = io.read_json(batch / 'manifest.json')
    if (manifest['kind'] != 'difficulty_submission_reconstruction'
            or manifest['model_requests'] != 0 or not manifest['training_prohibited']):
        raise ValueError('Not a difficulty-only clean reconstruction')
    binding = load(manifest['source_binding'])
    key = binding['task_key']
    if key not in REGISTRY:
        raise ValueError('Contract is outside this source-reconstruction adapter')
    policy_name, checker_name, initial_database = REGISTRY[key]
    original_trial = (ROOT / binding['original_result']['path']).parent
    original = load(binding['original_result'])
    original_capture, _, _ = verify_capture(original_trial, key)
    prepared = load(binding['prepared_model_run'])
    if prepared['identity']['task_key'] != key or prepared['identity']['task_version'] != binding['task_version']:
        raise ValueError('Task version mismatch')
    qualified = load(binding['qualified_review'])
    qb=load(qualified['phase3_identity']['batch_manifest'])
    qt=Path(qualified['phase3_identity']['batch_manifest']['path']).parent/'tasks'/qualified['phase3_identity']['label']
    qe=qb['tasks'][qualified['phase3_identity']['label']]
    if io.tree_hash(qt)!=qe['validation_copy_hash'] or not io.evidence_ok(qe['native_verifier'],ROOT):
        raise ValueError('Qualified task/native materialization context changed')
    if not qualified['eligible_for_sampling'] or qualified['audit']['conclusion'] != 'validated':
        raise ValueError('Checker is not qualified')
    overlay = load(qualified['audit']['overlay'])
    for code in overlay['code_refs']:
        if not io.evidence_ok(code, ROOT):
            raise ValueError('Qualified checker changed')
    config = load(manifest['config'])
    job = Path(config['jobs_dir']) / config['job_name']
    job_result = io.read_json(job / 'result.json')
    trial_results = sorted(job.glob('*/result.json'))
    if len(trial_results) != 1 or not job_result.get('finished_at') or config['n_attempts'] != 1:
        raise ValueError('Expected exactly one complete clean reconstruction')
    result_path = trial_results[0]
    result = io.read_json(result_path)
    trial = result_path.parent
    if not result.get('finished_at') or result.get('exception_info'):
        raise ValueError('Reconstruction failed; retain unknown until its cause is reviewed')
    task = Path(result['config']['task']['path'])
    if task.resolve() != Path(config['tasks'][0]['path']).resolve():
        raise ValueError('Unexpected reconstruction task')
    entry = manifest['tasks'][task.name]
    request = load(entry['request'])
    if io.tree_hash(task) != entry['validation_copy_hash'] or not io.evidence_ok(entry['native_verifier'], ROOT):
        raise ValueError('Reconstruction task or native verifier changed')
    execution_path = trial / 'agent/audit-rebuild/execution.json'
    ex = io.read_json(execution_path)
    base = execution_path.parent
    if (ex['task_key'] != key or ex['model_requests'] != 0
            or ex['spec_sha256'] != entry['request']['sha256']
            or ex['driver_sha256'] != io.file_hash(task / 'audit-rebuild/audit_rebuild_driver.py')):
        raise ValueError('Executed reconstruction identity mismatch')
    no_model = io.read_json(trial / 'agent/no-model.json')
    if no_model['model_requests'] != 0 or no_model['solution_uploaded'] is not False:
        raise ValueError('Unexpected solution/model access')
    if not ex['build_success'] or [x['command'] for x in ex['runs']] != request['commands']:
        raise ValueError('Declared clean build did not complete')
    if any(x['returncode'] != 0 for x in ex['runs']):
        raise ValueError('Clean build has failed commands')
    baseline = load(qualified['reproduction']['trials'][0]['execution'])
    original_requests = load(io.read_json(ROOT / qualified['phase3_identity']['batch_manifest']['path'])['requests'])
    base_request = next(x for x in original_requests['tasks'] if x['task_key'] == key)
    submission, names, plan = source_plan(binding['extracted'], baseline, base_request['rebuild'])
    if plan != binding['plan']:
        raise ValueError('Reconstructed source plan changed')
    assert_delivered_sources(ex, names, binding['extracted'])
    final_rows = inventory_map(ex, 'final_files')
    if any(final_rows.get(n, {}).get('exists') for n in submission['delete_paths']):
        raise ValueError('Deleted source was restored by reconstruction')

    def captured(name):
        row = final_rows[name]
        path = io.contained(base / 'final-files', row['file'])
        if not row['exists'] or row.get('error') or io.file_hash(path) != row['sha256']:
            raise ValueError('Fresh artifact changed: ' + name)
        return path

    here = ROOT / 'validation/ade' / key.removeprefix('ade__') / 'phase3_v1'
    policy = io.read_json(here / policy_name)
    if ex['initial_input_hashes'] != [{'path': initial_database,
                                      'sha256': policy['initial_database_sha256']}]:
        raise ValueError('Reconstruction uses different initial inputs')

    def hook(phase, filename):
        found=[]
        for h in ex[phase + '_capture_hooks']:
            if h['returncode'] != 0:raise ValueError('Independent capture hook failed')
            command=h['command'];flag='--output'if '--output'in command else '--output-dir'
            path=base / Path(command[command.index(flag) + 1]).name / filename
            if path.is_file():found.append(path)
        if len(found)!=1:raise ValueError('Capture filename absent or ambiguous: '+filename)
        return found[0]

    inputs = hook('initial', 'inputs.json')
    validate_initial(inputs, ROOT / policy['inputs']['path'])
    snapshot = trial / 'agent/audit-submission/snapshot.json'
    project_file = captured('dbt_project.yml')
    project = yaml.safe_load(project_file.read_text())
    model_paths = project.get('model-paths', ['models'])
    model_tree = {str(Path(t['relative_path']) / f['path']): f['sha256']
                  for t in ex['final_trees'] if t['relative_path'] in model_paths
                  for f in t['files'] if f['path'].endswith('.sql')}
    sources = [{'original_file_path': n, 'file': ref(captured(n))}
               for n in names if n.endswith('.sql')
               and any(Path(n).is_relative_to(Path(p)) for p in model_paths)]
    if {r['original_file_path']: r['file']['sha256'] for r in sources} != model_tree:
        raise ValueError('Configured model inventory is incomplete')
    run = ex['runs'][-1]
    log = base / run['log']
    if io.file_hash(log) != run['log_sha256']:
        raise ValueError('Build log changed')
    output.mkdir(parents=True)
    project_ref = save(output / 'project.json', {'model_paths': model_paths, 'source': ref(project_file)})
    workflow = {'producer': 'locked_qwen_clean_rebuild', 'discarded_candidate_dbt_artifacts': True,
        'task_key': key, 'snapshot': ref(snapshot), 'project': project_ref,
        'complete_model_inventory': True, 'sources': sources,
        'manifest': ref(captured('phase3_target/manifest.json')),
        'run_results': ref(captured('phase3_target/run_results.json')),
        'invocation': {'argv': run['command'], 'returncode': run['returncode'],
            'started_unix': datetime.fromisoformat(run['started_at_utc']).timestamp(),
            'finished_unix': datetime.fromisoformat(run['finished_at_utc']).timestamp(), 'log': ref(log)},
        'trust_evidence': [ref(batch / 'manifest.json'), manifest['source_binding'], ref(execution_path)],
        'scope': 'Fresh reconstruction of actual delivered source, not proof of model-issued commands.'}
    delivery=contract_record(qualified)
    delivery_ref=save(output / 'submission-contract.json',delivery)
    workflow['submission_contract']=delivery_ref
    workflow_ref = save(output / 'workflow.json', workflow)
    checker_request = {'task_key': key, 'snapshot': ref(snapshot), 'stage': 'agent-submission',
        'workflow': workflow_ref, 'actual_initial_capture': ref(inputs),
        'initial_database_sha256': policy['initial_database_sha256'],
        'clean_build_output': ref(hook('final', 'snapshot.json'))}
    session_review=None
    if key=='ade__ade-bench-f1009':
        original_clock=original_trial/'artifacts/difficulty-submission/dbt-session-clock.json'
        try:
            before_path=hook('initial','session-clock.json');after_path=hook('final','session-clock.json')
            original_clock_ref=ref(original_clock)
            session_review=review_session(io.read_json(original_clock),io.read_json(before_path),io.read_json(after_path),
                workflow,original['verifier'],binding['extracted']['capture'],ROOT)
            session_review.update(original_clock=original_clock_ref,before=ref(before_path),after=ref(after_path))
            session_ref=save(output/'session-review.json',session_review)
            if session_review['status']=='joined':
                context={'producer':'locked_qwen_clean_rebuild','dbt_session_timezone_matches':True,
                    'actual_database':'/app/f1.duckdb','before':ref(before_path),'after':ref(after_path),
                    'session_evidence':[session_ref,workflow_ref,original_clock_ref]}
                checker_request['clock_context']=save(output/'clock-context.json',context)
        except (ValueError,OSError,KeyError)as exc:
            session_review={'status':'unknown','reason':str(exc)}
            session_ref=save(output/'session-review.json',session_review)
    request_ref = save(output / 'request.json', checker_request)
    checker = here / checker_name
    if ref(checker) not in overlay['code_refs']:
        # References may be absolute or repository-relative, so compare paths.
        if not any((ROOT / x['path']).resolve() == checker.resolve() and x['sha256'] == io.file_hash(checker)
                   for x in overlay['code_refs']):
            raise ValueError('Scoring entrypoint is outside the qualified checker')
    spec = importlib.util.spec_from_file_location('difficulty_ade_source_checker', checker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        audit_result = module.run(checker_request, ROOT)
    except Exception as exc:
        audit_result={'task_key':key,'target':'audit','status':'unknown','reward':None,'reason':str(exc),'error_type':type(exc).__name__}
    if key=='ade__ade-bench-f1009' and session_review.get('status')!='joined':
        # Fresh clock probes are evidence of this rebuild, not of an earlier
        # model/native submission. Preserve them and fail closed until joined.
        clock_refs={phase:ref(hook(phase,'clock.json')) for phase in ('initial','final')}
        clock_review=save(output/'clock-review.json',{'task_key':key,'actual_clean_rebuild_sql_clocks':clock_refs,'original_capture':binding['extracted']['capture'],'original_native_result':binding['original_result'],'clock_relation':'unknown','session_review':session_ref,'reason':'Current source reconstruction has real SQL clock probes but the original submission capture has no reviewed original SQL date/timezone context; a fresh day cannot be imposed on an earlier valid source submission.','model_requests':0})
        audit_result={'task_key':key,'target':'audit','status':'unknown','reward':None,'clock_review':clock_review,'unjoined_checker_result':audit_result,'reason':'Original SQL clock relationship is not established.'}
    audit_result.update(task_key=key,target='audit',model_requests=0,training_prohibited=True)

    result_ref = save(output / 'audit-result.json', audit_result)
    receipt = {'schema_version': 1, 'task_key': key, 'task_version': binding['task_version'],
        'original_result': binding['original_result'], 'original_native_rewards':
            (original.get('verifier_result') or {}).get('rewards'),
        'original_capture': binding['extracted']['capture'], 'source_binding': manifest['source_binding'],
        'rebuild_result': ref(result_path), 'qualified_review': binding['qualified_review'],
        'checker_version': qualified['audit']['checker_version'], 'checker': ref(checker),
        'checker_request': request_ref, 'audit_result': result_ref,
        'audit_reward': audit_result['reward'], 'audit_status': audit_result['status'],
        'fixture_only': binding['fixture_only'], 'model_requests': 0, 'training_prohibited': True,
        'adapter': ref(Path(__file__)),
        'scope': 'Contract-scoped clean source reconstruction. Original native reward is unchanged.'}
    image_hash=prepared['identity']['backend_bindings'][key]['image_sha256']
    image=Path(tomllib.loads((task/'task.toml').read_text())['environment']['docker_image'])
    qualified_task=Path(qualified['phase3_identity']['batch_manifest']['path']).parent/'tasks'/qualified['phase3_identity']['label']
    qualified_image=Path(tomllib.loads((qualified_task/'task.toml').read_text())['environment']['docker_image'])
    if image.resolve()!=qualified_image.resolve():raise ValueError('Reconstruction image differs from the actual qualified review image')
    if not image.is_file() or io.file_hash(image)!=image_hash:
        raise ValueError('Actual task image does not match the qualified image')
    paths=[Path(__file__),ROOT/'scripts/difficulty_ade_registry.py',ROOT/'scripts/difficulty_ade_inputs.py',ROOT/'scripts/difficulty_ade_clock.py',ROOT/'scripts/difficulty_ade_session.py',ROOT/'scripts/difficulty_ade_session_collect.py',ROOT/'scripts/difficulty_ade_prepare_clock.py',ROOT/'scripts/prepare_difficulty_audit_v2.py',ROOT/'scripts/difficulty_submission.py',ROOT/'scripts/audit_rebuild_driver.py',ROOT/'scripts/prepare_audit_rebuild.py',ROOT/'scripts/mechanical_observer.py',ROOT/'scripts/context_probe.py',ROOT/'src/dbt_bench/io.py']
    code_refs=[ref(p)for p in paths]+manifest['tools']+[manifest['preparation_code']]
    for marker in (manifest.get('ade_failure_probe'),manifest.get('ade_sql_session_observer'),prepared['identity'].get('ade_sql_session_observer')):
        if marker:code_refs.extend(marker[k]for k in ('source','preparer'))
    deduplicated={}
    for dependency in code_refs:
        if not io.evidence_ok(dependency,ROOT):raise ValueError('Adapter or executed runtime dependency changed')
        deduplicated[str((ROOT/dependency['path']).resolve())]=dependency
    deps=save(output/'adapter-dependencies.json',{'code_refs':list(deduplicated.values()),'checker_dependencies':overlay['code_refs'],'image':ref(image),'submission_contract':delivery_ref,'model_requests':0})
    receipt.update(adapter_dependencies=deps,submission_contract=delivery_ref,submission_kind=delivery['submission_kind'],actual_model_dbt_execution_inferred=False,original_database_preserved=True)
    save(output / 'receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    answer = score(args.audit_dir, args.output)
    print({key: answer[key] for key in ('task_key', 'audit_reward', 'audit_status', 'fixture_only')})
