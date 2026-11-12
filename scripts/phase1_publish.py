#!/usr/bin/env python3
"""Root-only merge, reconcile and freeze native phase-1 reviews in groups of ten."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from review_tools import ROOT, cached_hashes
from evidence_archive import archive_review_refs
from phase1_mechanical import MECHANICAL, refs_in
from dbt_bench import io
from dbt_bench.catalog import load_catalog
from dbt_bench.validation import CHECKS, REF_KINDS, WITNESS_KINDS, build_record, identity, review_inputs, summarize, reusable

SEMANTIC = set(CHECKS) - set(MECHANICAL)
ALLOWED_UPDATES = {'workflow', 'inputs', 'deliverables', 'requirements', 'reference_kind',
                   'witness_kind', 'review_status', 'missing_contract_work', 'notes', 'native_success'}


def frozen_ref_ok(ref, archive):
    """A shared working file may evolve; the frozen hash must stay recoverable."""
    return io.evidence_ok(ref, ROOT) or io.evidence_ok({'path': str(Path(archive) / ref['sha256']),
                                                      'sha256': ref['sha256']}, ROOT)


def check_overlay(value, expected, base):
    if value.get('target') != 'native' or value['base_identity'] != base:
        raise ValueError('Overlay scope/identity mismatch: ' + value['task_key'])
    if set(value['checks']) != set(expected):
        raise ValueError('Wrong check set: ' + value['task_key'])
    updates = value.get('contract_updates', {})
    if updates.keys() - ALLOWED_UPDATES:
        raise ValueError('Unsupported contract updates: ' + value['task_key'] + ': ' +
                         ', '.join(sorted(updates.keys() - ALLOWED_UPDATES)))
    for field, allowed in (('reference_kind', REF_KINDS), ('witness_kind', WITNESS_KINDS)):
        if field in updates and updates[field] not in allowed:
            raise ValueError('Unknown ' + field + ': ' + value['task_key'])
    for name, check in value['checks'].items():
        if check.get('status') not in {'pass', 'fail', 'gap'} or not check.get('reason'):
            raise ValueError('Incomplete check: ' + name)
        if not check.get('evidence') or not all(io.evidence_ok(r, ROOT) for r in check['evidence']):
            raise ValueError('Missing/changed evidence: ' + value['task_key'] + ':' + name)
    if value.get('model_requests', 0) != 0:
        raise ValueError('Phase 1 forbids new model requests')


def merge_requirements(previous, incoming):
    """Preserve existing established rule proofs when adding review detail."""
    result = {r['id']: deepcopy(r) for r in previous}
    if len({r['id'] for r in incoming}) != len(incoming):
        raise ValueError('Duplicate incoming requirement IDs')
    for r in incoming:
        old = result.get(r['id'])
        if old:
            established = old.get('coverage', {}).get('native', {})
            if established.get('status') in {'missed_wrong', 'rejected_legal'}:
                if r.get('coverage', {}).get('native') != established:
                    r = {**r, 'coverage': {**r.get('coverage', {}), 'native': established}}
            # Supplemental checker coverage is outside this phase.
            if 'audit' in old.get('coverage', {}):
                r = {**r, 'coverage': {**r.get('coverage', {}), 'audit': old['coverage']['audit']}}
        result[r['id']] = r
    return list(result.values())


def verify_freeze(path):
    value = io.read_json(path / 'manifest.json')
    unsigned = {k: v for k, v in value.items() if k != 'hash'}
    assert io.digest(unsigned) == value['hash']
    for ref in (value['validation_snapshot'], value['candidate_accounting'], value['report']):
        assert io.evidence_ok(ref, ROOT)
    archive = value.get('content_archive', ROOT / 'artifacts/validation/phase1-freeze-objects')
    for field in ('retained_trial_index', 'freeze_script', 'source_lock', 'catalog', 'split'):
        assert frozen_ref_ok(value[field], archive), 'Frozen shared input unavailable: ' + field
    records = io.read_jsonl(ROOT / value['validation_snapshot']['path'])
    assert len(records) == len({r['task_key'] for r in records}) == 135
    # Final summaries explicitly retain pending=0; ledger counts omit empty bins.
    assert Counter(r['native']['conclusion'] for r in records) == Counter(value['native_counts'])
    completed = set(value['completed_task_keys'])
    assert all(all(c['status'] in {'pass', 'fail', 'gap'} for c in r['native']['checks'].values())
               for r in records if r['task_key'] in completed)
    assert len(value['isolated_broken']) == 4 and not completed.intersection(value['isolated_broken'])
    for snapshot in value['review_snapshots']:
        assert io.evidence_ok(snapshot['archived'], ROOT)
        assert snapshot['original']['sha256'] == snapshot['archived']['sha256']
    assert value['phase1_complete'] == (len(completed) == 131)
    assert not value['phase2_started'] and value['new_model_requests'] == 0
    return {'passed': True, 'tasks': 135, 'completed_native14': len(completed),
            'manifest': io.evidence_ref(path / 'manifest.json', ROOT)}


def freeze(root_review, state, rows, records, batch, previous_delivery):
    number = len(state['freezes']) + 1
    out = ROOT / 'catalog/pools' / f'native-phase1-v{number:02d}'
    if out.exists():
        raise ValueError('Refuse to overwrite frozen version: ' + str(out))
    out.mkdir()
    by_key = {r['task_key']: r for r in records}
    accounting = []
    snapshots = []
    archived_refs = set()
    object_store = (ROOT / 'artifacts/validation/phase1-freeze-objects').resolve()
    object_store.mkdir(parents=True, exist_ok=True)

    def archive_ref(task_key, original):
        token = (original['path'], original['sha256'])
        if token in archived_refs:
            return
        source = ROOT / original['path']
        data = source.read_bytes()
        import hashlib
        assert hashlib.sha256(data).hexdigest() == original['sha256'], 'Evidence changed during freeze: ' + str(source)
        archive = object_store / original['sha256']
        if archive.exists():
            assert io.file_hash(archive) == original['sha256']
        else:
            archive.write_bytes(data)
        snapshots.append({'task_key': task_key, 'original': original, 'archived': io.evidence_ref(archive, ROOT)})
        archived_refs.add(token)
    for row in rows:
        r = by_key[row['task_key']]
        reasons = []
        if row['split'] != 'train':
            reasons.append('not_train_split')
        if not r['sampling_eligible']:
            reasons.append('verifier_protocol_not_validated')
        accounting.append({'task_key': row['task_key'], 'source': row['source'], 'split': row['split'],
            'task_family': row['task_family'], 'legacy_split': row['legacy_split'], 'exposure_history': row['exposure_history'],
            'native': r['native']['conclusion'], 'audit': r['audit']['conclusion'], 'identity_hash': r['identity_hash'],
            'native14_reviewed': row['task_key'] in state['completed'],
            'eligible_for_this_pool': not reasons, 'exclusion_reasons': reasons})
        # Canonical review files are mutable. Freeze their exact bytes and a map
        # from original refs; older frozen versions remain independently readable.
        directory = (ROOT / row['validation_ref']).parent
        for filename in ('contract.yaml', 'record.json', 'environment.json'):
            source = directory / filename
            if source.exists():
                original = io.evidence_ref(source, ROOT)
                archive_ref(row['task_key'], original)
        # Review directories and shared scripts can evolve after this freeze.
        # Archive their referenced bytes by hash; raw trial artifacts stay at
        # their immutable job paths and are never rewritten by this publisher.
        for ref in r.get('evidence', []):
            source = ROOT / ref['path']
            relative = str(source.absolute())
            mutable = any(marker in relative for marker in ('/phase1-review-v1/', '/validation/ade/',
                '/validation/deb/', '/validation/spider2/', '/scripts/'))
            if mutable and source.is_file():
                archive_ref(row['task_key'], ref)
    io.write_jsonl(out / 'candidates.jsonl', accounting)
    io.write_jsonl(out / 'validation-ledger.jsonl', records)
    summary = io.read_json(ROOT / 'validation/summary.json')
    active = [r for r in records if r['task_key'] not in state['isolated_broken']]
    counts = Counter(c['status'] for r in active for c in r['native']['checks'].values())
    completed = len(state['completed'])
    eligible = [a['task_key'] for a in accounting if a['eligible_for_this_pool']]
    report = (f'# Native phase 1 — {number:02d}\n\n'
              f'135 candidates: 4 broken isolated, {completed}/131 native14 reviews complete.\n\n'
              f'Non-broken check dispositions: {dict(counts)}. A gap records an actually executed but inconclusive check; unexecuted work stays pending.\n\n'
              f'Native conclusions: {summary["native"]}; audit: {summary["audit"]}.\n\n'
              f'Eligible train-split sampling tasks: {len(eligible)}. Full verifier validation complete: false. '
              f'Phase 2 started: false. New model requests: 0. Native rewards modified: false.\n\n'
              '[Manifest](manifest.json), [task accounting](candidates.jsonl), [ledger snapshot](validation-ledger.jsonl).\n')
    io.write_text(out / 'report.md', report)
    # These shared inputs will evolve. Frozen versions point to their archived
    # bytes rather than mutable working files.
    frozen_shared = {}
    for name, source in {'retained_trial_index': root_review / 'retained-trial-index.json',
                         'freeze_script': Path(__file__)}.items():
        ref = io.evidence_ref(source, ROOT)
        archive_ref('shared', ref)
        frozen_shared[name] = io.evidence_ref(object_store / ref['sha256'], ROOT)
    manifest = {'schema_version': 1, 'version': out.name, 'candidate_count': 135,
        'completed_task_keys': sorted(state['completed']), 'completed_native14': completed,
        'isolated_broken': state['isolated_broken'], 'newly_completed': batch,
        'phase1_complete': completed == 131, 'phase2_started': False,
        'implementation_complete': False, 'validation_complete': False,
        'new_model_requests': 0, 'native_rewards_modified': False,
        'native_counts': summary['native'], 'audit_counts': summary['audit'],
        'active_native_check_counts': dict(counts), 'eligible_task_count': len(eligible), 'eligible_tasks': eligible,
        'candidate_accounting': io.evidence_ref(out / 'candidates.jsonl', ROOT),
        'validation_snapshot': io.evidence_ref(out / 'validation-ledger.jsonl', ROOT),
        'report': io.evidence_ref(out / 'report.md', ROOT), 'review_snapshots': snapshots,
        'source_lock': io.evidence_ref(ROOT / 'sources.lock.yaml', ROOT),
        'catalog': io.evidence_ref(ROOT / 'catalog/tasks.jsonl', ROOT),
        'split': io.evidence_ref(ROOT / 'catalog/splits.json', ROOT),
        'previous_freeze': state['freezes'][-1] if state['freezes'] else previous_delivery['frozen_sampling_pool'],
        'retained_trial_index': frozen_shared['retained_trial_index'],
        'freeze_script': frozen_shared['freeze_script'],
        'content_archive': str(object_store),
        'scope': 'Native14 evidence-review progress; gap is an acknowledged limitation. No supplemental audit changes.'}
    manifest['hash'] = io.digest(manifest)
    io.write_json(out / 'manifest.json', manifest)
    integrity = verify_freeze(out)
    io.write_json(out / 'integrity.json', integrity)
    state['freezes'].append(integrity['manifest'])
    delivery = deepcopy(previous_delivery)
    trial_counts = io.read_json(root_review / 'retained-trial-index.json')['counts']
    delivery.update(timestamp_utc=datetime.now(timezone.utc).isoformat(), native=summary['native'],
        native_check_counts=summary['native_check_counts'], missing_real_witness=summary['missing_real_witness'],
        real_native_validation_trials=trial_counts['native'], additional_input_fixture_trials=trial_counts['additional_fixture'],
        protocol_checks_pending_execution_or_review=summary['native_check_counts'].get('pending', 0),
        reviewed_protocol_checks=sum(n for s, n in summary['native_check_counts'].items() if s != 'pending'),
        frozen_sampling_pool=integrity['manifest'], frozen_pilot_pool=integrity['manifest'], frozen_pools=[integrity['manifest']],
        phase1_native14={'completed': completed, 'target': 131, 'isolated_broken': 4,
                         'complete': completed == 131, 'active_check_counts': dict(counts), 'phase2_started': False},
        implementation_complete=False, validation_complete=False,
        unfinished_validation=['Native phase 1 progress is separate from full semantic certification. Gaps and witness deficits remain explicit.',
                               'Supplemental audits are deferred until phase 1 is complete and reported to the user.'])
    delivery['historical_frozen_pools'] = delivery.get('historical_frozen_pools', []) + [previous_delivery['frozen_sampling_pool']]
    io.write_json(ROOT / 'validation/delivery-status.json', delivery)
    io.write_json(root_review / 'state.json', state)
    return integrity


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review-root', type=Path, default=ROOT / 'artifacts/validation/phase1-review-v1')
    p.add_argument('--mechanical', type=Path, default=ROOT / 'artifacts/validation/phase1-mechanical-v1')
    p.add_argument('--final-remainder', action='store_true')
    p.add_argument('--task', action='append', help='Root-reviewed selection; does not bypass evidence checks')
    p.add_argument('--verify', type=Path)
    a = p.parse_args()
    if a.verify:
        print(verify_freeze(a.verify))
        return
    root_review = a.review_root
    if not io.read_json(a.mechanical / 'summary.json').get('execution_backed'):
        raise ValueError('Mechanical template gap review is retained for history only; execution-backed review required')
    rows = load_catalog(ROOT)
    by_row = {r['task_key']: r for r in rows}
    records = io.read_jsonl(ROOT / 'validation/ledger.jsonl')
    by_record = {r['task_key']: r for r in records}
    state_path = root_review / 'state.json'
    state = io.read_json(state_path) if state_path.exists() else {
        'completed': [], 'freezes': [], 'isolated_broken': sorted(k for k, r in by_record.items() if r['native']['conclusion'] == 'broken')}
    assignments = io.read_json(root_review / 'assignments.json')['groups']
    available = {}
    paused = io.read_json(root_review / 'spider-pause-20261106.json').get('pause_semantic_agents', False) if (root_review / 'spider-pause-20261106.json').exists() else False
    for group, keys in assignments.items():
        for path in (root_review / group).glob('*/semantic.json'):
            semantic = io.read_json(path)
            key = semantic['task_key']
            assert key in keys and key not in available and key not in state['isolated_broken']
            if paused and key.startswith('spider2__'):
                continue
            if a.task and key not in a.task:
                continue
            if key not in state['completed']:
                checks = semantic.get('checks', {})
                if set(checks) == SEMANTIC and all(c.get('status') in {'pass', 'fail', 'gap'} for c in checks.values()):
                    current_contract = io.read_json((ROOT / by_row[key]['validation_ref']).parent / 'contract.yaml')
                    requirements = semantic.get('contract_updates', {}).get('requirements', current_contract.get('requirements', []))
                    if any(r.get('critical', True) and r.get('coverage', {}).get('native', {}).get('status') not in
                           {'covered', 'missed_wrong', 'rejected_legal', 'gap'} for r in requirements):
                        continue
                    mechanical_path = a.mechanical / key / 'mechanical.json'
                    if mechanical_path.exists():
                        mechanical_checks = io.read_json(mechanical_path).get('checks', {})
                        if set(mechanical_checks) == set(MECHANICAL) and all(c.get('status') in {'pass', 'fail', 'gap'} for c in mechanical_checks.values()):
                            available[key] = path
    ready = sorted(available)
    with cached_hashes():
        while len(ready) >= 10 or (a.final_remainder and ready):
            batch = ready[:10]
            ready = ready[10:]
            # A failed, rolled-back merge remains evidence. A later corrected
            # attempt gets its own backup directory rather than deleting it.
            attempt = 1
            while True:
                backup = root_review / 'merges' / f'{len(state["freezes"]) + 1:02d}-attempt{attempt:02d}'
                if not backup.exists():
                    break
                attempt += 1
            backup.mkdir(parents=True, exist_ok=False)
            saved = {}
            try:
                for key in batch:
                    row = by_row[key]
                    record = by_record[key]
                    directory = ROOT / 'validation' / row['source'] / row['upstream_task_id']
                    path = directory / 'contract.yaml'
                    current = identity(ROOT, row, path, 'apptainer')
                    mechanical = io.read_json(a.mechanical / key / 'mechanical.json')
                    semantic = io.read_json(available[key])
                    assert mechanical['base_identity'] == current, 'Task changed since mechanical review: ' + key
                    check_overlay(mechanical, MECHANICAL, current)
                    check_overlay(semantic, SEMANTIC, current)
                    updates = semantic.get('contract_updates', {})
                    assert not (updates.keys() - ALLOWED_UPDATES), key
                    old = io.read_json(path)
                    new = deepcopy(old)
                    if 'requirements' in updates:
                        updates = {**updates, 'requirements': merge_requirements(old.get('requirements', []), updates['requirements'])}
                    new.update(updates)
                    for requirement in new.get('requirements', []):
                        coverage = requirement.get('coverage', {}).get('native', {})
                        if coverage.get('status') in {'missed_wrong', 'rejected_legal'}:
                            expected = 'business_error' if coverage['status'] == 'missed_wrong' else 'legal_equivalent'
                            assert coverage.get('case_validity') == expected, (key, requirement['id'], 'missing case validity')
                            assert all(io.evidence_ok(coverage.get(field), ROOT) for field in
                                       ('independent_proof', 'checker_evidence')), (key, requirement['id'], 'missing adjudication binding')
                    new['checks'] = {**mechanical['checks'], **semantic['checks']}
                    if new != old:
                        # Root owns review history and identity re-binding.
                        for name in ('contract.yaml', 'record.json'):
                            f = directory / name
                            saved[f] = f.read_bytes()
                            dest = backup / key / name
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(saved[f])
                        new.setdefault('review_history', []).append(io.evidence_ref(backup / key / 'contract.yaml', ROOT))
                        new.setdefault('review_evidence', []).extend([
                            io.evidence_ref(a.mechanical / key / 'mechanical.json', ROOT),
                            io.evidence_ref(available[key], ROOT), *semantic.get('review_evidence', [])])
                        new['reviewed_inputs'] = review_inputs(current)
                        new = archive_review_refs(new, ROOT, ROOT / 'artifacts/validation/phase1-freeze-objects',
                                                  preserve_audit=True)
                        io.write_json(path, new)
                        updated = build_record(ROOT, row, path, record.get('execution'), backend='apptainer')
                        assert updated['review_current'] and not updated['blockers'], key
                        if record['audit']['conclusion'] == 'validated':
                            assert updated['audit'] == record['audit'], 'Validated audit changed: ' + key
                        else:
                            assert new.get('audit') == old.get('audit'), 'Audit contract changed: ' + key
                            assert all(updated['audit'].get(k) == record['audit'].get(k) for k in ('conclusion', 'checker_version', 'checks')), 'Audit execution changed: ' + key
                        assert all(c['status'] in {'pass', 'fail', 'gap'} for c in updated['native']['checks'].values()), key
                        if record['native']['conclusion'] == 'weak':
                            assert updated['native']['conclusion'] == 'weak', 'Lost established weakness: ' + key
                        assert reusable(updated, updated['identity'], ROOT), key
                        io.write_json(directory / 'record.json', updated)
                        by_record[key] = updated
                    else:
                        assert all(c['status'] in {'pass', 'fail', 'gap'} for c in record['native']['checks'].values())
                records = [by_record[r['task_key']] for r in rows]
            except BaseException:
                for path, data in saved.items():
                    path.write_bytes(data)
                io.write_json(backup / 'rollback.json', {'restored_files': len(saved),
                    'canonical_restored': all(path.read_bytes() == data for path, data in saved.items()),
                    'ledger_modified': False, 'freeze_created': False})
                raise
            state['completed'].extend(batch)
            summary = summarize(ROOT, rows, records)
            previous_delivery = io.read_json(ROOT / 'validation/delivery-status.json')
            integrity = freeze(root_review, state, rows, records, batch, previous_delivery)
            print(integrity, flush=True)
    print({'waiting_ready_tasks': len(ready), 'completed': len(state['completed'])})


if __name__ == '__main__':
    main()
