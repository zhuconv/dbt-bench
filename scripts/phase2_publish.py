#!/usr/bin/env python3
"""Root-only stage/merge/freeze for audit14 reviews; native conclusions stay exact."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import shutil

from dbt_bench import io
from dbt_bench.validation import CHECKS, build_record, review_inputs, summarize
from phase2_review import precheck
from review_tools import ROOT, cached_hashes


def stage(review, overlay_path):
    review = Path(review)
    state = io.read_json(review / 'state.json')
    value = precheck(ROOT, overlay_path)
    if not value['audit14_review_complete']:
        raise ValueError('Partial audit14 cannot be staged as completed')
    key = value['task_key']
    inventory = io.read_json(ROOT / state['inventory']['path'])
    targets = {x['task_key'] for x in inventory['tasks'] if x['audit_status'] != 'existing_validated_retained'}
    if key not in targets:
        raise ValueError('Task is outside the new-audit queue; retain existing validated audits')
    ready = state.setdefault('ready_overlays', {})
    if key in ready or key in state['newly_completed_audit14']:
        raise ValueError('Task already staged/published: ' + key)
    directory = review / 'ready' / key
    directory.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(overlay_path, directory / 'overlay.json')
    io.write_json(directory / 'precheck.json', value)
    ready[key] = {'overlay': io.evidence_ref(directory / 'overlay.json', ROOT),
                  'precheck': io.evidence_ref(directory / 'precheck.json', ROOT)}
    io.write_json(review / 'state.json', state)
    delivery_path = ROOT / 'validation/delivery-status.json'
    delivery = io.read_json(delivery_path)
    delivery.update(phase2_state=io.evidence_ref(review / 'state.json', ROOT), phase2_reviews_ready=len(ready))
    io.write_json(delivery_path, delivery)
    return {'staged': key, 'audit_conclusion': value['conclusion'], 'ready': len(ready)}


def validate_dependency_extension(old, new):
    """An unpublished provenance correction cannot change scores or judgments."""
    if {k: v for k, v in old.items() if k != 'code_refs'} != {k: v for k, v in new.items() if k != 'code_refs'}:
        raise ValueError('Dependency amendment cannot change audit judgments or identity')
    before = {str((ROOT / r['path']).resolve()): r['sha256'] for r in old['code_refs']}
    after = {str((ROOT / r['path']).resolve()): r['sha256'] for r in new['code_refs']}
    if len(before) != len(old['code_refs']) or len(after) != len(new['code_refs']):
        raise ValueError('Duplicate dependency path')
    if not before.keys() < after.keys() or any(after[k] != v for k, v in before.items()):
        raise ValueError('Only additional unchanged-code dependencies may be declared')


def extend_staged_dependencies(review, overlay_path):
    review = Path(review)
    state = io.read_json(review / 'state.json')
    new = io.read_json(overlay_path)
    key = new['task_key']
    if key in state['newly_completed_audit14'] or key not in state['ready_overlays']:
        raise ValueError('Dependency amendment requires an unpublished staged review')
    previous = state['ready_overlays'][key]
    if not all(io.evidence_ok(ref, ROOT) for ref in previous.values()):
        raise ValueError('Previous staged review is no longer intact')
    old = io.read_json(ROOT / previous['overlay']['path'])
    validate_dependency_extension(old, new)
    checked = precheck(ROOT, overlay_path)
    old_check = io.read_json(ROOT / previous['precheck']['path'])
    if (not checked['audit14_review_complete'] or checked['conclusion'] != old_check['conclusion']
            or checked['check_counts'] != old_check['check_counts']):
        raise ValueError('Dependency amendment changed review completion/conclusion')
    directory = review / 'ready' / key / 'dependency-amendments' / io.digest(new)
    directory.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(overlay_path, directory / 'overlay.json')
    io.write_json(directory / 'precheck.json', checked)
    current = {n: io.evidence_ref(directory / (n + '.json'), ROOT) for n in ('overlay', 'precheck')}
    io.write_json(directory / 'amendment.json', {
        'task_key': key, 'previous': previous, 'current': current,
        'reason': 'Declare previously omitted transitive code dependencies; actual code, checks and outcomes unchanged.',
        'model_requests': 0, 'benchmark_reruns': 0,
    })
    state['ready_overlays'][key] = current
    state.setdefault('staged_dependency_amendments', []).append(io.evidence_ref(directory / 'amendment.json', ROOT))
    io.write_json(review / 'state.json', state)
    delivery_path = ROOT / 'validation/delivery-status.json'
    delivery = io.read_json(delivery_path)
    delivery['phase2_state'] = io.evidence_ref(review / 'state.json', ROOT)
    io.write_json(delivery_path, delivery)
    return {'amended_dependencies': key, 'audit_conclusion': checked['conclusion'],
            'ready': len(state['ready_overlays'])}


def stage_revision(review, overlay_path, reason):
    """Queue a fully re-reviewed checker version, retaining the prior audit bytes."""
    if not reason or not reason.strip():
        raise ValueError('Explain the audit revision')
    review = Path(review)
    state = io.read_json(review / 'state.json')
    checked = precheck(ROOT, overlay_path)
    key = checked['task_key']
    if not checked['audit14_review_complete'] or key not in state['newly_completed_audit14']:
        raise ValueError('Revision requires a completed, previously published new-audit task')
    if key in state.get('ready_revisions', {}) or key in state['ready_overlays']:
        raise ValueError('Task already queued')
    rows = io.unique_index(io.read_jsonl(ROOT / 'catalog/tasks.jsonl'), 'task_key')
    prior = ROOT / rows[key]['validation_ref']
    directory = review / 'revisions' / key / io.file_hash(overlay_path)
    directory.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(overlay_path, directory / 'overlay.json')
    shutil.copyfile(prior, directory / 'previous-record.json')
    io.write_json(directory / 'precheck.json', checked)
    entry = {n: io.evidence_ref(directory / (n + '.json'), ROOT)
             for n in ('overlay', 'precheck', 'previous-record')}
    io.write_json(directory / 'revision.json', {'task_key': key, 'reason': reason,
        'previous_record': entry['previous-record'], 'replacement_overlay': entry['overlay'],
        'new_completed_task_credit': 0, 'native_rewards_modified': False})
    entry['revision'] = io.evidence_ref(directory / 'revision.json', ROOT)
    state.setdefault('ready_revisions', {})[key] = entry
    io.write_json(review / 'state.json', state)
    p = ROOT / 'validation/delivery-status.json'; delivery = io.read_json(p)
    delivery.update(phase2_state=io.evidence_ref(review / 'state.json', ROOT),
                    phase2_revisions_ready=len(state['ready_revisions']))
    io.write_json(p, delivery)
    return {'revision_staged': key, 'conclusion': checked['conclusion'], 'new_completed_task_credit': 0}


def publication_selection(state, target, final_remainder=False):
    done = set(state['newly_completed_audit14'])
    new = sorted(state.get('ready_overlays', {}))
    revised = sorted(state.get('ready_revisions', {}))
    if not done <= target or set(new) & done or not set(new) <= target or not set(revised) <= done:
        raise ValueError('Invalid new/revised task accounting')
    if final_remainder:
        if not new or len(new) >= 10 or set(new) | done != target:
            raise ValueError('Final remainder requires every remaining weak-task audit14 review')
    elif len(new) < 10:
        if not (not new and revised and done == target):
            raise ValueError('Wait for ten completed reviews before freezing; revisions do not count as new tasks')
    else:
        new = new[:10]
    return new, revised


def merge_audit(contract, overlay):
    """Keep every native contract field byte-equivalent as a JSON value."""
    result = deepcopy(contract)
    requirements = deepcopy(contract['requirements'])
    for req in requirements:
        if req['id'] in overlay['requirement_coverage']:
            req.setdefault('coverage', {})['audit'] = overlay['requirement_coverage'][req['id']]
    result['audit'] = {k: deepcopy(overlay[k]) for k in
        ('checker_version', 'code_refs', 'review_status', 'reference_kind', 'witness_kind', 'scope', 'checks')}
    result['audit'].update(reviewed_inputs=review_inputs(overlay['base_identity']),
                           requirements=requirements, diagnoses=overlay.get('diagnoses', []))
    if {k: v for k, v in result.items() if k != 'audit'} != {k: v for k, v in contract.items() if k != 'audit'}:
        raise ValueError('Native contract unexpectedly changed')
    return result


def archive_mutable_refs(value, mutable_paths, save, root):
    """Keep reviewed pre-merge bytes available after canonical files change.

    Apply only to the new audit overlay, never to the native contract branch.
    """
    mutable = {Path(p).resolve() for p in mutable_paths}
    historical = {}
    def aliases(item):
        if isinstance(item, dict):
            original, immutable = item.get('original'), item.get('immutable')
            if (isinstance(original, dict) and isinstance(immutable, dict)
                    and {'path', 'sha256'} <= original.keys()
                    and {'path', 'sha256'} <= immutable.keys()):
                source = (Path(root) / original['path']).resolve()
                if source in mutable:
                    if (original['sha256'] != immutable['sha256']
                            or (Path(root) / immutable['path']).resolve() in mutable
                            or not io.evidence_ok(immutable, root)):
                        raise ValueError('Invalid explicit historical reference alias: ' + str(source))
                    historical[(source, original['sha256'])] = immutable
            for child in item.values(): aliases(child)
        elif isinstance(item, list):
            for child in item: aliases(child)
    # Only an explicit original/immutable pair can resolve historical metadata.
    # The active checks have already passed precheck against their own paths.
    # Never search for a similar file or accept an unverified content hash.
    aliases(value)
    def visit(item):
        if isinstance(item, dict):
            if {'path', 'sha256'} <= item.keys() and (Path(root) / item['path']).resolve() in mutable:
                ref = item
                if not io.evidence_ok(item, root):
                    ref = historical.get(((Path(root) / item['path']).resolve(), item['sha256']), item)
                return {**item, **save(ref)}
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        return item
    return visit(value)


def assert_audit_preserved(record, overlay, expected):
    if record['audit']['checks'] != overlay['checks']:
        raise ValueError('Audit checks changed during merge: ' + record['task_key'])
    if record['audit']['conclusion'] != expected['conclusion']:
        raise ValueError('Audit conclusion differs from precheck: ' + record['task_key'])


def verify_freeze(path):
    path = Path(path)
    manifest = io.read_json(path / 'manifest.json')
    if io.digest({k: v for k, v in manifest.items() if k != 'hash'}) != manifest['hash']:
        raise ValueError('Freeze manifest hash mismatch')
    for name in ('ledger', 'baseline_ledger', 'candidates', 'report', 'catalog', 'split', 'source_lock'):
        if not io.evidence_ok(manifest[name], ROOT):
            raise ValueError('Missing frozen artifact: ' + name)
    for ref in manifest['archived_review_inputs']:
        if not io.evidence_ok(ref, ROOT): raise ValueError('Missing archived audit review input')
    if manifest.get('precheck_history_resolution') and not io.evidence_ok(manifest['precheck_history_resolution'], ROOT):
        raise ValueError('Missing historical precheck input resolution')
    records = io.unique_index(io.read_jsonl(ROOT / manifest['ledger']['path']), 'task_key')
    baseline = io.unique_index(io.read_jsonl(ROOT / manifest['baseline_ledger']['path']), 'task_key')
    if set(records) != set(baseline): raise ValueError('Candidate coverage differs from native freeze')
    if any(records[k]['native'] != baseline[k]['native'] for k in records):
        raise ValueError('Native adjudication/check evidence changed')
    revised = set(manifest.get('revised_reviews', []))
    if revised & set(manifest.get('newly_reviewed', [])) or not revised <= set(manifest['completed_new_audit14']):
        raise ValueError('Revised task counted as a new review')
    for ref in manifest.get('revision_records', {}).values():
        if not io.evidence_ok(ref, ROOT): raise ValueError('Missing audit revision history')
    if Counter(r['audit']['conclusion'] for r in records.values()) != Counter(manifest['audit_counts']):
        raise ValueError('Audit count mismatch')
    if Counter(r['native']['conclusion'] for r in records.values()) != Counter(manifest['native_counts']):
        raise ValueError('Native count mismatch')
    for key in manifest.get('completed_new_audit14', []):
        checks = records[key]['audit']['checks']
        if set(checks) != set(CHECKS) or any(c['status'] not in {'pass', 'fail', 'gap'} for c in checks.values()):
            raise ValueError('Frozen completed audit14 contains missing/pending checks: ' + key)
    for key, ref in manifest.get('new_review_prechecks', {}).items():
        if not io.evidence_ok(ref, ROOT): raise ValueError('Missing archived audit precheck')
        expected = io.read_json(ROOT / ref['path'])
        if manifest.get('precheck_inputs_archived'):
            if not io.evidence_ok(expected['base_record'], ROOT):
                raise ValueError('Missing immutable precheck base record')
            previous = io.read_json(ROOT / expected['base_record']['path'])
            if previous['task_key'] != key or previous['native'] != baseline[key]['native']:
                raise ValueError('Precheck input differs from native task history')
        audit = records[key]['audit']
        if audit['conclusion'] != expected['conclusion'] or Counter(c['status'] for c in audit['checks'].values()) != Counter(expected['check_counts']):
            raise ValueError('Frozen audit differs from reviewed precheck: ' + key)
    catalog = io.unique_index(io.read_jsonl(ROOT / manifest['catalog']['path']), 'task_key')
    candidates = io.unique_index(io.read_jsonl(ROOT / manifest['candidates']['path']), 'task_key')
    if set(catalog) != set(records) or set(candidates) != set(records):
        raise ValueError('Frozen pool membership differs from candidate accounting')
    for key, candidate in candidates.items():
        record = records[key]
        if candidate['split'] != catalog[key]['split']:
            raise ValueError('Frozen split was relabeled')
        if candidate['native'] != record['native']['conclusion'] or candidate['audit'] != record['audit']['conclusion']:
            raise ValueError('Pool conclusion differs from ledger')
        eligible = catalog[key]['split'] == 'train' and record['sampling_eligible']
        if candidate['eligible_for_sampling'] is not eligible:
            raise ValueError('Test/unvalidated candidate admitted to pool')
    return {'passed': True, 'candidate_count': len(records), 'manifest': io.evidence_ref(path / 'manifest.json', ROOT)}


def publish(review, final_remainder=False, *, attempt=1):
    if type(attempt) is not int or attempt < 1:
        raise ValueError('Publication attempt must be a positive integer')
    review = Path(review)
    state = io.read_json(review / 'state.json')
    inventory = io.read_json(ROOT / state['inventory']['path'])
    target = {x['task_key'] for x in inventory['tasks'] if x['audit_status'] != 'existing_validated_retained'}
    done = set(state['newly_completed_audit14'])
    new_keys, revision_keys = publication_selection(state, target, final_remainder)
    keys = new_keys + revision_keys
    staged = {**state.get('ready_overlays', {}), **state.get('ready_revisions', {})}
    rows = io.read_jsonl(ROOT / 'catalog/tasks.jsonl')
    by_row = io.unique_index(rows, 'task_key')
    records = io.read_jsonl(ROOT / 'validation/ledger.jsonl')
    by_record = io.unique_index(records, 'task_key')
    overlays, prechecks = {}, {}
    with cached_hashes():
        for key in keys:
            ref = staged[key]['overlay']
            if not io.evidence_ok(ref, ROOT): raise ValueError('Staged review changed')
            path = ROOT / ref['path']
            check = precheck(ROOT, path)
            if not check['audit14_review_complete']: raise ValueError('Staged review incomplete')
            prechecks[key] = check
            overlays[key] = io.read_json(path)
            if key in revision_keys:
                previous = staged[key]['previous-record']
                if not io.evidence_ok(previous, ROOT) or io.read_json(ROOT / previous['path']) != by_record[key]:
                    raise ValueError('Previously published task changed before revision')
    number = state.get('next_freeze_number', len(state['freezes']) + 1)
    version = f'audit-phase2-v{number:02d}'
    out = ROOT / 'catalog/pools' / version
    artifact = review / 'freezes' / (version if attempt == 1 else f'{version}-attempt-{attempt}')
    if out.exists() or artifact.exists(): raise ValueError('Refuse to overwrite freeze')
    artifact.mkdir(parents=True)
    paths = [ROOT / 'validation/ledger.jsonl', ROOT / 'validation/summary.json',
             ROOT / 'validation/delivery-status.json', ROOT / 'docs/validation-status.md', review / 'state.json']
    for key in keys:
        p = ROOT / by_row[key]['validation_ref']; paths.extend([p, p.parent / 'contract.yaml'])
    backups = {p: p.read_bytes() for p in paths}
    for index, (path, data) in enumerate(backups.items()):
        (artifact / f'before-{index:03d}').write_bytes(data)
    archive = artifact / 'objects'; archive.mkdir()
    archived = {}
    def save(ref):
        if not io.evidence_ok(ref, ROOT): raise ValueError('Changed review input during publish: ' + str(ref))
        if ref['sha256'] not in archived:
            dest = archive / ref['sha256']; shutil.copyfile(ROOT / ref['path'], dest)
            archived[ref['sha256']] = io.evidence_ref(dest, ROOT)
        return archived[ref['sha256']]
    try:
        with cached_hashes():
            # Snapshot every affected mutable reference before changing any task.
            overlays = {key: archive_mutable_refs(value, paths, save, ROOT) for key, value in overlays.items()}
            archived_prechecks = {}
            for key in keys:
                save(staged[key]['precheck'])  # Retain the original review receipt.
                checked = archive_mutable_refs(prechecks[key], paths, save, ROOT)
                checked_path = artifact / 'prechecks' / (key + '.json')
                io.write_json(checked_path, checked)
                archived_prechecks[key] = save(io.evidence_ref(checked_path, ROOT))
            revision_records = {key: save(staged[key]['revision']) for key in revision_keys}
            for key in revision_keys: save(staged[key]['previous-record'])
            for key in keys:
                path = (ROOT / by_row[key]['validation_ref']).parent / 'contract.yaml'
                contract = io.read_json(path)
                io.write_json(path, merge_audit(contract, overlays[key]))
                record = build_record(ROOT, by_row[key], path, execution=by_record[key].get('execution'),
                                      backend=by_record[key]['identity']['backend'])
                if record['native'] != by_record[key]['native']:
                    raise ValueError('Native record changed on audit-only merge: ' + key)
                assert_audit_preserved(record, overlays[key], prechecks[key])
                by_record[key] = record
                io.write_json(ROOT / by_row[key]['validation_ref'], record)
                save(io.evidence_ref(path, ROOT)); save(staged[key]['overlay'])
                for ref in overlays[key]['code_refs']: save(ref)
                for check in overlays[key]['checks'].values():
                    for ref in check.get('evidence', []) + check.get('execution_evidence', []):
                        if '/phase2-review-v1/' in ref['path'] or '/audit_phase2_v1/' in ref['path'] or '/scripts/' in ref['path']:
                            save(ref)
        records = [by_record[row['task_key']] for row in rows]
        summary = summarize(ROOT, rows, records)
        io.write_jsonl(artifact / 'ledger.jsonl', records)
        candidates = [{'task_key': row['task_key'], 'split': row['split'],
            **{key: deepcopy(row[key]) for key in ('source', 'task_family', 'project_family',
                'warehouse_family', 'legacy_split', 'exposure_history') if key in row},
            'native': by_record[row['task_key']]['native']['conclusion'],
            'audit': by_record[row['task_key']]['audit']['conclusion'],
            'eligible_for_sampling': row['split'] == 'train' and by_record[row['task_key']]['sampling_eligible']}
            for row in rows]
        out.mkdir(parents=True)
        io.write_jsonl(out / 'candidates.jsonl', candidates)
        completed = sorted(done | set(new_keys))
        io.write_text(out / 'report.md', f'# {version}\n\nNew weak-task audit14 reviews: {len(completed)}/{len(target)}; '
            f'one existing validated audit retained. Native records unchanged.\n\nAudit conclusions: {summary["audit"]}. '
            f'Eligible train-split sampling tasks: {sum(x["eligible_for_sampling"] for x in candidates)}. '
            'A completed review with gap is not a validated audit. No model requests.\n'
            '\nTask eligibility only certifies the declared verification protocol. Each training-candidate '
            'trace must independently satisfy the native-success, audit, environment and trace rules; '
            'audit success never replaces a native zero reward.\n'
            + (f'\nRevised existing audits: {", ".join(revision_keys)}. These do not count as new tasks.\n' if revision_keys else ''))
        manifest = {'schema_version': 1, 'version': version, 'publication_attempt': attempt,
            'baseline_ledger': inventory['baseline_ledger'],
            'ledger': io.evidence_ref(artifact / 'ledger.jsonl', ROOT),
            'candidates': io.evidence_ref(out / 'candidates.jsonl', ROOT), 'report': io.evidence_ref(out / 'report.md', ROOT),
            'newly_reviewed': new_keys, 'revised_reviews': revision_keys, 'revision_records': revision_records,
            'completed_new_audit14': completed, 'new_audit14_target': len(target),
            'phase2_review_complete': set(completed) == target, 'native_counts': summary['native'],
            'new_review_prechecks': archived_prechecks,
            'precheck_inputs_archived': True,
            'audit_counts': summary['audit'], 'native_rewards_modified': False, 'model_requests': 0,
            'archived_review_inputs': list(archived.values()),
            'publication_recovery': state.get('invalidated_freezes', []),
            'precheck_history_resolution': state.get('precheck_history_resolution'),
            'previous_freeze': state['freezes'][-1] if state['freezes'] else inventory['phase1_freeze']}
        for name, path in {'catalog': ROOT / 'catalog/tasks.jsonl', 'split': ROOT / 'catalog/splits.json',
                           'source_lock': ROOT / 'sources.lock.yaml'}.items():
            ref = io.evidence_ref(path, ROOT); save(ref)
            manifest[name] = archived[ref['sha256']]
        manifest['archived_review_inputs'] = list(archived.values())
        manifest['hash'] = io.digest(manifest)
        io.write_json(out / 'manifest.json', manifest)
        integrity = verify_freeze(out); io.write_json(out / 'integrity.json', integrity)
        state['newly_completed_audit14'] = completed
        for key in new_keys: del state['ready_overlays'][key]
        for key in revision_keys: del state['ready_revisions'][key]
        state['freezes'].append(integrity['manifest'])
        state['next_freeze_number'] = number + 1
        state['phase2_integrity_recovery_pending'] = False
        state['phase2_complete'] = set(completed) == target
        io.write_json(review / 'state.json', state)
        delivery = io.read_json(ROOT / 'validation/delivery-status.json')
        eligible_keys = [c['task_key'] for c in candidates if c['eligible_for_sampling']]
        delivery.update(phase2_started=True, phase2_review_complete=state['phase2_complete'],
            phase2_complete=state['phase2_complete'], phase2_reviews_ready=len(state['ready_overlays']),
            phase2_revisions_ready=len(state.get('ready_revisions', {})),
            phase2_state=io.evidence_ref(review / 'state.json', ROOT), audit=summary['audit'],
            audit_check_counts=summary['audit_check_counts'],
            latest_audit_freeze=integrity['manifest'], frozen_sampling_pool=integrity['manifest'],
            sampling_eligible_tasks=len(eligible_keys), sampling_eligible_task_keys=eligible_keys,
            implementation_complete=False, validation_complete=False)
        delivery.setdefault('frozen_pools', []).append(integrity['manifest'])
        io.write_json(ROOT / 'validation/delivery-status.json', delivery)
        return integrity
    except Exception:
        for path, data in backups.items(): path.write_bytes(data)
        if out.exists(): shutil.move(str(out), str(artifact / 'failed-unpublished-pool'))
        # The failed attempt and backups are retained; retry uses a distinct
        # artifact directory after root inspection, never an overwrite.
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review-root', type=Path, default=ROOT / 'artifacts/validation/phase2-review-v1')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--stage', type=Path); mode.add_argument('--publish', action='store_true'); mode.add_argument('--verify', type=Path)
    mode.add_argument('--extend-staged-dependencies', type=Path)
    mode.add_argument('--stage-revision', type=Path)
    p.add_argument('--revision-reason')
    p.add_argument('--final-remainder', action='store_true')
    p.add_argument('--publication-attempt', type=int, default=1,
                   help='Explicit new artifact directory for a retained failed publication; never overwrite a pool')
    a = p.parse_args()
    if a.publication_attempt != 1 and not a.publish:
        p.error('--publication-attempt only applies to --publish')
    print(stage(a.review_root, a.stage) if a.stage else
          extend_staged_dependencies(a.review_root, a.extend_staged_dependencies) if a.extend_staged_dependencies else
          stage_revision(a.review_root, a.stage_revision, a.revision_reason) if a.stage_revision else
          publish(a.review_root, a.final_remainder, attempt=a.publication_attempt) if a.publish else verify_freeze(a.verify))
