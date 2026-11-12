#!/usr/bin/env python3
"""Stage and freeze additive DEB test reviews; historical splits and train rows stay exact."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import shutil
from dbt_bench import io
from dbt_bench.backends import resolve_task
from phase4_review import precheck
from phase2_publish import validate_dependency_extension
from review_tools import ROOT, cached_hashes


def verify_protected(review, root=ROOT):
    refs = io.read_json(Path(review) / 'protected-files.json')
    changed = [r['path'] for r in refs if not io.evidence_ok(r, root)]
    if changed:
        raise ValueError('Protected baseline changed: ' + repr(changed))
    return len(refs)


def record_exclusion(review, path, root=ROOT):
    """Record reviewed external triage without inventing new benchmark scores."""
    review, path, root = map(Path, (review, path, root))
    verify_protected(review, root)
    state = io.read_json(review / 'state.json')
    inventory = io.read_json(root / state['inventory']['path'])
    value = io.read_json(path); key = value['task_key']
    allowed = {r['task_key']: r for r in inventory['tasks'] if not r['formal_execution_required']}
    if key not in allowed or value.get('category') != allowed[key]['category']:
        raise ValueError('Not an authorized phase4 exclusion')
    if (value.get('model_requests') != 0 or value.get('review_status') != 'reviewed'
            or not value.get('reason') or value.get('eligible_for_sampling') is not False
            or value.get('previous_record') != allowed[key]['previous_record']):
        raise ValueError('Incomplete exclusion review')
    refs = value.get('evidence', [])
    if not refs or any(not io.evidence_ok(r, root) for r in refs):
        raise ValueError('Missing or changed exclusion evidence')
    if key in state.get('excluded_reviews', {}):
        raise ValueError('Exclusion already recorded; preserve prior review')
    target = review / 'excluded' / key / 'review.json'
    target.parent.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(path, target)
    state.setdefault('excluded_reviews', {})[key] = io.evidence_ref(target, root)
    io.write_json(review / 'state.json', state)
    return {'task_key': key, 'excluded': True, 'record': state['excluded_reviews'][key]}


def reproduction(reconciliation, expected, root=ROOT):
    """Use raw Harbor reward and driver outcomes, never a caller's success flag."""
    value = io.read_json(reconciliation)
    if not value['complete'] or value['model_requests'] != 0 or value['errors']:
        raise ValueError('Reconstruction batch is not reconciled')
    if value['manifest'] != expected['batch_manifest']:
        raise ValueError('Reconciliation belongs to a different formal batch')
    trials = [r for r in value['trials'] if r['label'] == expected['label']]
    if len(trials) != 2 or len({r['trial_id'] for r in trials}) != 2:
        raise ValueError('Require exactly two independent clean formal trials')
    success = []
    for trial in trials:
        for field in ('result', 'execution'):
            if not io.evidence_ok(trial[field], root):
                raise ValueError('Raw reproduction evidence changed: ' + field)
        raw = io.read_json(Path(root) / trial['result']['path'])
        execution = io.read_json(Path(root) / trial['execution']['path'])
        rewards = (raw.get('verifier_result') or {}).get('rewards')
        if rewards != trial['native_rewards'] or raw.get('exception_info') != trial['exception_info']:
            raise ValueError('Reconciliation does not match original Harbor result')
        if execution.get('build_success') != trial['build_success']:
            raise ValueError('Reconciliation does not match original build outcome')
        success.append(rewards == {'reward': 1.0} and not raw.get('exception_info') and execution.get('build_success') is True)
    return {'native1_both_clean_builds': all(success), 'trials': trials,
            'reconciliation': io.evidence_ref(reconciliation, root)}


def field_at(value, path):
    if not isinstance(path, list) or not path:
        raise ValueError('Require an explicit nonempty JSON field path')
    try:
        for part in path:
            if type(part) not in (str, int):
                raise ValueError('Invalid JSON field path')
            value = value[part]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError('Declared audit field is absent') from exc
    return value


def binary_score(value):
    if value is not None and (type(value) not in (int, float) or value not in (0, 1)):
        raise ValueError('Unsupported candidate audit score')
    return value


def candidate_evaluation(path, expected, rebuilt, root=ROOT, qualified_overlay=None):
    """Bind an explicit candidate score separately from checker qualification."""
    value = io.read_json(path)
    if (value.get('phase4_identity') != expected or value.get('task_key') != expected['task_key']
            or value.get('model_requests') != 0):
        raise ValueError('Candidate review identity differs')
    if qualified_overlay is None:
        raise ValueError('Candidate scores must identify their reviewed checker')
    if not io.evidence_ok(value.get('qualified_overlay', {}), root):
        raise ValueError('Qualified checker overlay changed or missing')
    qualified = io.read_json(Path(root) / value['qualified_overlay']['path'])
    if (qualified != qualified_overlay or value.get('checker_version') != qualified['checker_version']
            or value.get('code_refs') != qualified['code_refs']
            or any(not io.evidence_ok(ref, root) for ref in qualified['code_refs'])):
        raise ValueError('Candidate score checker differs from the reviewed checker')
    directories = {(Path(root) / t['result']['path']).parent.name: (Path(root) / t['result']['path']).parent.resolve()
                   for t in rebuilt['trials']}
    if len(directories) != 2:
        raise ValueError('Require two distinct formal trial directories')
    stages = {'agent-submission': 'agent/audit-submission',
              'verifier-before': 'verifier/mechanical-before', 'verifier-after': 'verifier/mechanical-after'}
    wanted = {(trial, stage) for trial in directories for stage in stages}
    seen, scores = set(), []
    for row in value['records']:
        key = (row['trial'], row['stage'])
        if key not in wanted or key in seen:
            raise ValueError('Unknown or duplicate candidate trial/stage')
        seen.add(key)
        for name in ('candidate_snapshot', 'audit_result'):
            if not io.evidence_ok(row[name], root):
                raise ValueError('Candidate evidence changed: ' + name)
        snapshot_path = Path(root) / row['candidate_snapshot']['path']
        if snapshot_path.resolve() != directories[row['trial']] / stages[row['stage']] / 'snapshot.json':
            raise ValueError('Candidate snapshot belongs to a different formal trial')
        snapshot = io.read_json(snapshot_path)
        audit = io.read_json(Path(root) / row['audit_result']['path'])
        if snapshot['task_key'] != expected['task_key'] or snapshot['stage'] != row['stage']:
            raise ValueError('Candidate snapshot task/stage differs')
        paths = row.get('audit_field_paths')
        if paths is not None:
            if set(paths) != {'reward', 'candidate_snapshot', 'task_key'}:
                raise ValueError('Declare exactly score, snapshot and optional task field paths')
            if (field_at(audit, paths['candidate_snapshot']) != row['candidate_snapshot']
                    or paths['task_key'] is not None and field_at(audit, paths['task_key']) != expected['task_key']):
                raise ValueError('Raw audit is not bound to its actual formal submission')
            score = binary_score(field_at(audit, paths['reward']))
        else:
            if not io.evidence_ok(row.get('spec', {}), root):
                raise ValueError('Candidate evidence changed: spec')
            spec = io.read_json(Path(root) / row['spec']['path'])
            if (spec['candidate_snapshot'] != row['candidate_snapshot'] or spec['candidate_stage'] != row['stage']
                    or spec['task_key'] != expected['task_key'] or audit.get('task_key') != expected['task_key']
                    or audit.get('input_refs', {}).get('candidate_snapshot') != row['candidate_snapshot']):
                raise ValueError('Audit score is not bound to its actual formal submission')
            score = binary_score(audit.get('reward'))
        if row.get('workflow_result'):
            if not io.evidence_ok(row['workflow_result'], root):
                raise ValueError('Candidate workflow evidence changed')
            workflow = io.read_json(Path(root) / row['workflow_result']['path'])
            workflow_score = binary_score(field_at(workflow, row['workflow_reward_path']))
            score = 0 if 0 in (score, workflow_score) else 1 if score == workflow_score == 1 else None
        if score != row['audit_reward']:
            raise ValueError('Audit score is not bound to its actual formal submission')
        scores.append(binary_score(row['audit_reward']))
    if seen != wanted:
        raise ValueError('Require all six formal candidate stage scores')
    passed = all(score == 1 for score in scores)
    if value.get('candidate_pass') is not passed:
        raise ValueError('Declared candidate outcome disagrees with actual scores')
    return {'status': 'pass' if passed else 'fail' if 0 in scores else 'unknown',
            'passed': passed, 'scores': scores, 'review': io.evidence_ref(path, root),
            'scope': 'Actual formal submission scores; independent of checker protocol qualification.'}


def stage(review, native_path, audit_path, reconciliation, root=ROOT, candidate_review=None):
    review, root = Path(review), Path(root)
    verify_protected(review, root)
    with cached_hashes():
        audit = precheck(audit_path, root)
        native = precheck(native_path, root) if native_path else None
    if native is None:
        raise ValueError('Phase4 requires a current native14 overlay for every formal task')
    rebuilt = reproduction(reconciliation, audit['phase4_identity'], root)
    if candidate_review is None:
        raise ValueError('Require the actual six formal candidate audit scores; checker qualification alone is insufficient')
    candidate = candidate_evaluation(candidate_review, audit['phase4_identity'], rebuilt, root,
                                     qualified_overlay=io.read_json(audit_path))
    if native['target'] != 'native' or audit['target'] != 'audit' or native['phase4_identity'] != audit['phase4_identity']:
        raise ValueError('Native and audit must review the same submission/task/environment')
    if not native['review_complete'] or not audit['review_complete']:
        raise ValueError('Pending checks cannot be frozen as reviewed')
    key = native['task_key']
    state = io.read_json(review / 'state.json')
    if key in state['ready'] or key in state['completed']:
        raise ValueError('Task already staged or frozen')
    inventory = io.read_json(root / state['inventory']['path'])
    allowed = {r['task_key']: r for r in inventory['tasks'] if r['formal_execution_required']}
    if key not in allowed or native['category'] != allowed[key]['category']:
        raise ValueError('Task/category outside phase 4 queue')
    batch_manifest = io.read_json(root / audit['phase4_identity']['batch_manifest']['path'])
    if batch_manifest['pilot_results'] != inventory['pilot_results']:
        raise ValueError('Formal batch does not use the fixed phase4 pilot inventory')
    folder = review / 'ready' / key
    folder.mkdir(parents=True, exist_ok=False)
    for name, path in [('native-overlay', native_path), ('audit-overlay', audit_path)]:
        if path:
            shutil.copyfile(path, folder / (name + '.json'))
    checks = io.read_json(audit_path)['checks']
    eligible = (rebuilt['native1_both_clean_builds'] and audit['conclusion'] == 'validated'
                and native['conclusion'] in {'weak', 'validated'}
                and candidate['passed']
                and all(checks[n]['status'] == 'pass' for n in ('environment_agent', 'environment_verifier', 'cross_context', 'agent_visibility')))
    record = {'schema_version': 1, 'task_key': key, 'split': 'test',
        'task_version': native['task_version'], 'category': native['category'],
        'phase4_identity': native['phase4_identity'], 'native': native, 'audit': audit,
        'reproduction': rebuilt, 'eligible_for_sampling': eligible, 'eligible_for_measurement': eligible,
        'training_use_allowed': False, 'model_requests': 0,
        'formal_candidate_evaluation': candidate,
        'native_rewards_modified': False,
        'overlays': {n: io.evidence_ref(folder / (n + '-overlay.json'), root)
                     if (folder / (n + '-overlay.json')).exists() else None for n in ('native', 'audit')}}
    io.write_json(folder / 'record.json', record)
    state['ready'][key] = io.evidence_ref(folder / 'record.json', root)
    io.write_json(review / 'state.json', state)
    return {'task_key': key, 'audit': audit['conclusion'], 'eligible': eligible, 'ready': len(state['ready'])}


def merge_candidates(base, records, variants=None):
    rows = deepcopy(base)
    indexed = {r['task_key']: r for r in rows}
    for key, rec in records.items():
        row = indexed[key]
        if row['split'] != 'test' or rec['split'] != 'test' or row['source'] != 'deb':
            raise ValueError('A phase 4 review is limited to authorized DEB test')
        row.update(eligible_for_sampling=rec['eligible_for_sampling'], eligible_for_measurement=rec['eligible_for_sampling'],
                   training_use_allowed=False, task_version=rec['task_version'],
                   phase4_category=rec['category'], native=rec['native']['conclusion'],
                   audit=rec['audit']['conclusion'], phase4_record=rec['_ref'])
        row.pop('phase4_selection_reason', None)
        if variants and key in variants:
            version = variants[key]
            if version['task_version'] != rec['task_version']:
                raise ValueError('Production task version differs from reviewed version')
            row.update(run_path=version['run_path'], task_version_metadata=version['metadata'])
    if [r for r in rows if r['split'] == 'train'] != [r for r in base if r['split'] == 'train']:
        raise ValueError('Historical train rows changed')
    return rows


def amend_dependencies(review, audit_path, root=ROOT):
    """Add omitted immutable dependency refs before freezing, retaining the old record."""
    review, root = Path(review), Path(root)
    verify_protected(review, root)
    value = io.read_json(audit_path); key = value['task_key']
    state = io.read_json(review / 'state.json')
    if key not in state['ready'] or key in state['completed']:
        raise ValueError('Only an unpublished staged review can receive a dependency amendment')
    prior_ref = state['ready'][key]
    if not io.evidence_ok(prior_ref, root):
        raise ValueError('Original staged record changed')
    prior = io.read_json(root / prior_ref['path'])
    old = io.read_json(root / prior['overlays']['audit']['path'])
    comparable = deepcopy(value)
    note = comparable.pop('amendment', None)
    if note is not None:
        if (set(note) != {'original', 'reason'} or not note['reason']
                or not io.evidence_ok(note['original'], root)
                or note['original']['sha256'] != prior['overlays']['audit']['sha256']):
            raise ValueError('Dependency amendment note must identify the exact previous overlay')
    validate_dependency_extension(old, comparable)
    checked = precheck(audit_path, root)
    if not checked['review_complete'] or checked['conclusion'] != prior['audit']['conclusion'] or checked['check_counts'] != prior['audit']['check_counts']:
        raise ValueError('Dependency amendment changes judgments')
    folder = review / 'ready' / key / 'dependency-amendments' / io.file_hash(audit_path)
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(audit_path, folder / 'audit-overlay.json')
    updated = deepcopy(prior)
    updated.update(audit=checked, previous_companion=prior_ref,
                   amendment='Only additional previously executed immutable dependencies declared; judgments and trials unchanged.')
    updated['overlays']['audit'] = io.evidence_ref(folder / 'audit-overlay.json', root)
    io.write_json(folder / 'record.json', updated)
    state['ready'][key] = io.evidence_ref(folder / 'record.json', root)
    io.write_json(review / 'state.json', state)
    return {'task_key': key, 'amended_dependencies': True, 'ready': len(state['ready'])}


def amend_candidate(review, candidate_path, root=ROOT):
    """Add explicit actual candidate scores; preserve prior records and freezes."""
    review, root = Path(review), Path(root)
    verify_protected(review, root)
    state = io.read_json(review / 'state.json')
    candidate = io.read_json(candidate_path); key = candidate['task_key']
    section = 'ready' if key in state['ready'] else 'completed'
    if key not in state[section]:
        raise ValueError('Candidate amendment requires an existing reviewed task')
    prior_ref = state[section][key]
    if not io.evidence_ok(prior_ref, root):
        raise ValueError('Prior companion changed')
    prior = io.read_json(root / prior_ref['path'])
    overlay_ref = prior['overlays']['audit']
    if not io.evidence_ok(overlay_ref, root):
        raise ValueError('Audit overlay changed')
    overlay = io.read_json(root / overlay_ref['path'])
    result = candidate_evaluation(candidate_path, prior['phase4_identity'], prior['reproduction'], root, qualified_overlay=overlay)
    eligible = (prior['reproduction']['native1_both_clean_builds'] and prior['audit']['conclusion'] == 'validated'
                and prior['native']['conclusion'] in {'weak', 'validated'} and result['passed']
                and all(overlay['checks'][n]['status'] == 'pass' for n in ('environment_agent', 'environment_verifier', 'cross_context', 'agent_visibility')))
    folder = review / 'candidate-amendments' / key / io.file_hash(candidate_path)
    folder.mkdir(parents=True, exist_ok=False)
    updated = {**prior, 'formal_candidate_evaluation': result, 'eligible_for_sampling': eligible,
               'eligible_for_measurement': eligible, 'training_use_allowed': False,
               'previous_companion': prior_ref, 'amendment': 'Bind six actual formal candidate scores and their reviewed checker; original trials, judgments and freezes retained.'}
    io.write_json(folder / 'record.json', updated)
    state[section][key] = io.evidence_ref(folder / 'record.json', root)
    io.write_json(review / 'state.json', state)
    return {'task_key': key, 'candidate': result['status'], 'eligible': eligible, 'section': section}


def freeze(review, final=False, root=ROOT):
    review, root = Path(review), Path(root)
    count = verify_protected(review, root)
    state = io.read_json(review / 'state.json')
    inventory = io.read_json(root / state['inventory']['path'])
    remaining = inventory['formal_tasks'] - len(state['completed'])
    selected = sorted(state['ready'])[:10]
    if len(selected) < 10 and not (final and len(selected) == remaining and selected):
        raise ValueError('Freeze ten reviewed tasks, or the final exact remainder')
    exclusions = state.get('excluded_reviews', {})
    required_exclusions = {r['task_key'] for r in inventory['tasks'] if not r['formal_execution_required']}
    if set(exclusions) - required_exclusions or any(not io.evidence_ok(x, root) for x in exclusions.values()):
        raise ValueError('Invalid or changed exclusion review')
    if len(selected) == remaining and set(exclusions) != required_exclusions:
        raise ValueError('Final phase4 review also requires the three explicit exclusion reviews')
    number = len(state.get('freezes', [])) + 1
    version = f'test-phase4-v{number:02d}'
    directory = review / 'freezes' / version
    if directory.exists():
        raise ValueError('Frozen output already exists')
    combined = {**state['completed'], **{k: state['ready'][k] for k in selected}}
    records = {}
    with cached_hashes():
        for key, ref in combined.items():
            if not io.evidence_ok(ref, root):
                raise ValueError('Staged record changed')
            rec = io.read_json(root / ref['path'])
            if not rec.get('formal_candidate_evaluation'):
                raise ValueError('Explicit actual candidate scores required before a new freeze: ' + key)
            evaluated = candidate_evaluation(root / rec['formal_candidate_evaluation']['review']['path'],
                rec['phase4_identity'], rec['reproduction'], root,
                qualified_overlay=io.read_json(root / rec['overlays']['audit']['path']))
            if evaluated != rec['formal_candidate_evaluation'] or not evaluated['passed'] and rec['eligible_for_sampling']:
                raise ValueError('Candidate audit evidence changed or failure was admitted')
            for name in ('native', 'audit'):
                if rec['overlays'][name] is None:
                    raise ValueError('Phase4 requires both current native14 and audit14 overlays')
                if not io.evidence_ok(rec['overlays'][name], root):
                    raise ValueError('Staged overlay changed')
                # Recheck underlying outputs, cases, code and environment, not
                # just the small overlay file, immediately before publication.
                checked = precheck(root / rec['overlays'][name]['path'], root)
                if checked['conclusion'] != rec[name]['conclusion'] or not checked['review_complete']:
                    raise ValueError('Staged adjudication changed')
            records[key] = {**rec, '_ref': ref}
    versions_path = root / 'catalog/phase4-task-versions.json'
    versions = io.read_json(versions_path)['variants'] if versions_path.exists() else {}
    catalog = io.unique_index(io.read_jsonl(root / 'catalog/tasks.jsonl'), 'task_key')
    for key, rec in records.items():
        if not rec['phase4_identity']['task_variant']:
            versions[key] = {'task_version': rec['task_version'],
                'run_path': str(resolve_task(root, catalog[key], 'apptainer')), 'metadata': None}
    env_path = root / 'catalog/phase4-environment-versions.json'
    environments = io.read_json(env_path)['environments'] if env_path.exists() else {}
    for key, rec in records.items():
        extension = rec['phase4_identity'].get('environment_extension')
        if extension:
            if key not in environments or environments[key]['layer'] != extension['summary']:
                raise ValueError('Reviewed environment has no matching production task')
            entry = environments[key]
            if not io.evidence_ok(entry['metadata'], root) or io.tree_hash(entry['run_path']) != entry['production_task_hash']:
                raise ValueError('Production environment task changed')
            versions[key] = entry
    for key, rec in records.items():
        if not rec['phase4_identity']['task_variant']:
            continue
        if key not in versions or not io.evidence_ok(versions[key]['metadata'], root):
            raise ValueError('Reviewed task variant lacks an intact production task')
        metadata = io.read_json(root / versions[key]['metadata']['path'])
        if (metadata['production_task_hash'] != io.tree_hash(versions[key]['run_path'])
                or metadata['instruction_after']['sha256'] != rec['phase4_identity']['instruction_sha256']
                or metadata['native_verifier_modified']):
            raise ValueError('Production variant differs from the reviewed task')
    candidates = merge_candidates(io.read_jsonl(review / 'candidates-v00.jsonl'), records, versions)
    directory.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, directory / 'publisher.py')
    shutil.copyfile(root / 'scripts/phase4_review.py', directory / 'reviewer.py')
    io.write_jsonl(directory / 'candidates.jsonl', candidates)
    io.write_jsonl(directory / 'ledger.jsonl', [records[k] for k in sorted(records)])
    io.write_jsonl(directory / 'exclusions.jsonl', [{**io.read_json(root / ref['path']), '_ref': ref}
                                                  for key, ref in sorted(exclusions.items())])
    by_category = {}
    for category in ('agree', 'patched_pass'):
        selected_records = [r for r in records.values() if r['category'] == category]
        by_category[category] = {'reviewed': len(selected_records),
            'audit_validated': sum(r['audit']['conclusion'] == 'validated' for r in selected_records),
            'formal_candidate_audit_failures': sum((r.get('formal_candidate_evaluation') or {}).get('status') == 'fail' for r in selected_records),
            'eligible': sum(r['eligible_for_sampling'] for r in selected_records)}
    manifest = {'schema_version': 1, 'version': version, 'newly_reviewed': selected,
        'reviewed_total': len(records), 'formal_tasks': inventory['formal_tasks'],
        'phase4_review_complete': len(records) == inventory['formal_tasks'],
        'excluded_reviews': len(exclusions),
        'exclusions': io.evidence_ref(directory / 'exclusions.jsonl', root),
        'counts_by_category': by_category,
        'train_eligible': sum(r['split'] == 'train' and r['eligible_for_sampling'] for r in candidates),
        'previous_freeze': state['latest_freeze'], 'inventory': state['inventory'],
        'candidates': io.evidence_ref(directory / 'candidates.jsonl', root),
        'ledger': io.evidence_ref(directory / 'ledger.jsonl', root),
        'task_versions': io.evidence_ref(versions_path, root) if versions_path.exists() else None,
        'environment_versions': io.evidence_ref(env_path, root) if env_path.exists() else None,
        'publisher': io.evidence_ref(directory / 'publisher.py', root),
        'reviewer': io.evidence_ref(directory / 'reviewer.py', root),
        'protected_baseline_files_verified': count, 'train_candidates_unchanged': True,
        'phase4_test_eligible': sum(r['split'] == 'test' and r['eligible_for_sampling'] for r in candidates),
        'split_changed': False,
        'model_requests': 0, 'native_rewards_modified': False}
    io.write_json(directory / 'manifest.json', manifest)
    ref = io.evidence_ref(directory / 'manifest.json', root)
    state['completed'] = combined
    for key in selected:
        del state['ready'][key]
    state.setdefault('freezes', []).append(ref)
    state['latest_freeze'] = ref
    io.write_json(review / 'state.json', state)
    link = root / 'catalog/pools' / version
    if link.exists():
        raise ValueError('Pool version already exists')
    link.symlink_to(directory, target_is_directory=True)
    io.write_json(root / 'catalog/phase4-current.json', {'schema_version': 1, 'selection': ref,
                   'scope': 'Phase4 evaluation eligibility; historical split and train-phase3-v06 retained. No new train/test split is frozen here.'})
    return {'version': version, 'reviewed': len(records), 'train_eligible': manifest['train_eligible'], 'phase4_test_eligible': manifest['phase4_test_eligible'], 'counts': by_category}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review', type=Path, required=True)
    sub = p.add_subparsers(dest='action', required=True)
    s = sub.add_parser('stage')
    s.add_argument('--native', required=True, type=Path, help='Current native14 overlay required for every phase4 task')
    s.add_argument('--audit', required=True, type=Path)
    s.add_argument('--reconciliation', required=True, type=Path)
    s.add_argument('--candidate-review', required=True, type=Path, help='Separate actual formal candidate scores from checker protocol qualification')
    f = sub.add_parser('freeze')
    f.add_argument('--final', action='store_true')
    d = sub.add_parser('amend-dependencies')
    d.add_argument('--audit', required=True, type=Path)
    c = sub.add_parser('amend-candidate')
    c.add_argument('--candidate-review', required=True, type=Path)
    e = sub.add_parser('exclude'); e.add_argument('--exclusion-review', required=True, type=Path)
    a = p.parse_args()
    print(stage(a.review, a.native, a.audit, a.reconciliation, candidate_review=a.candidate_review) if a.action == 'stage' else
          amend_dependencies(a.review, a.audit) if a.action == 'amend-dependencies' else
          amend_candidate(a.review, a.candidate_review) if a.action == 'amend-candidate' else
          record_exclusion(a.review, a.exclusion_review) if a.action == 'exclude' else freeze(a.review, a.final))
