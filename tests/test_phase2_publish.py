"""Audit-only merging cannot alter native decisions or invent early freezes."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dbt_bench import io
from phase2_publish import (archive_mutable_refs, assert_audit_preserved, merge_audit, publish,
                           verify_freeze, validate_dependency_extension, extend_staged_dependencies)
from phase2_publish import publication_selection, stage_revision
from dbt_bench.validation import CHECKS, _check


class AuditPublicationTests(unittest.TestCase):
    def test_publish_ten_new_reviews_and_revision_retains_prior_audit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); review = root / 'review'; review.mkdir()
            keys = [f't{i:02d}' for i in range(20)]
            checks = {name: {'status': 'pass', 'reason': 'Synthetic execution evidence'} for name in CHECKS}
            records = []; rows = []; overlays = {}
            for i, key in enumerate(keys):
                record = {'task_key': key, 'identity': {'backend': 'fixture'},
                          'native': {'conclusion': 'weak', 'checks': {'negatives': {'status': 'fail'}}},
                          'audit': {'conclusion': 'validated' if i < 10 else 'pending',
                                    'checker_version': 'previous', 'checks': deepcopy(checks)},
                          'sampling_eligible': i < 10}
                records.append(record)
                rows.append({'task_key': key, 'validation_ref': f'validation/{key}/record.json',
                             'split': 'test' if key == 't00' else 'train'})
                io.write_json(root / rows[-1]['validation_ref'], record)
                io.write_json(root / f'validation/{key}/contract.yaml',
                              {'task_key': key, 'requirements': [], 'audit': deepcopy(record['audit'])})
                overlays[key] = {'task_key': key, 'checker_version': 'replacement', 'review_status': 'reviewed',
                    'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs',
                    'scope': 'Synthetic publication integration fixture', 'checks': deepcopy(checks),
                    'code_refs': [], 'requirement_coverage': {}, 'base_identity': {}}
            original_record = (root / rows[0]['validation_ref']).read_bytes()
            historical = root / 'historical-contract.json'
            io.write_json(historical, {'task_key': 't00', 'requirements': [], 'audit': 'older audit'})
            historical_ref = io.evidence_ref(historical, root)
            overlays['t00']['amendment'] = {'historical_contract': {
                'original': {**historical_ref, 'path': 'validation/t00/contract.yaml'},
                'immutable': historical_ref}}
            io.write_jsonl(root / 'catalog/tasks.jsonl', rows)
            io.write_jsonl(root / 'validation/ledger.jsonl', records)
            io.write_jsonl(root / 'baseline.jsonl', records)
            for path in ('catalog/splits.json', 'sources.lock.yaml', 'validation/summary.json',
                         'validation/delivery-status.json'):
                io.write_json(root / path, {})
            io.write_text(root / 'docs/validation-status.md', 'Previous report')
            io.write_json(root / 'inventory.json', {
                'tasks': [{'task_key': key, 'audit_status': 'pending'} for key in keys],
                'baseline_ledger': io.evidence_ref(root / 'baseline.jsonl', root),
                'phase1_freeze': io.evidence_ref(root / 'baseline.jsonl', root)})
            state = {'inventory': io.evidence_ref(root / 'inventory.json', root),
                     'newly_completed_audit14': keys[:10], 'ready_overlays': {}, 'freezes': []}
            def check(_root, path):
                key = io.read_json(path)['task_key']
                return {'task_key': key, 'audit14_review_complete': True,
                        'base_record': io.evidence_ref(root / f'validation/{key}/record.json', root),
                        'conclusion': 'validated', 'check_counts': {'pass': len(CHECKS)}}
            for key in keys[10:]:
                p = review / 'ready' / key / 'overlay.json'; io.write_json(p, overlays[key])
                io.write_json(p.parent / 'precheck.json', check(root, p))
                state['ready_overlays'][key] = {n: io.evidence_ref(p.parent / (n + '.json'), root)
                                               for n in ('overlay', 'precheck')}
            io.write_json(review / 'state.json', state)
            replacement = root / 'revision.json'; io.write_json(replacement, overlays['t00'])
            def build(_root, row, contract_path, **_kwargs):
                record = deepcopy(records[keys.index(row['task_key'])])
                record['audit'] = {**io.read_json(contract_path)['audit'], 'conclusion': 'validated'}
                record['sampling_eligible'] = True
                return record
            def summary(_root, _rows, merged):
                io.write_jsonl(root / 'validation/ledger.jsonl', merged)
                return {'native': {'weak': 20}, 'audit': {'validated': 20},
                        'audit_check_counts': {'pass': 20 * len(CHECKS)}}
            with patch('phase2_publish.ROOT', root), patch('phase2_publish.precheck', side_effect=check), \
                 patch('phase2_publish.build_record', side_effect=build), \
                 patch('phase2_publish.summarize', side_effect=summary):
                stage_revision(review, replacement, 'Actual legal representation previously rejected')
                self.assertEqual((root / rows[0]['validation_ref']).read_bytes(), original_record)
                failed = review / 'freezes/audit-phase2-v01'
                failed.mkdir(parents=True)
                (failed / 'retained-failure.txt').write_text('original failure evidence')
                with self.assertRaises(ValueError): publish(review)
                result = publish(review, attempt=2)
                self.assertTrue(result['passed'])
                manifest = io.read_json(root / result['manifest']['path'])
                self.assertEqual(manifest['publication_attempt'], 2)
                self.assertIn('audit-phase2-v01-attempt-2', manifest['ledger']['path'])
                self.assertEqual((failed / 'retained-failure.txt').read_text(), 'original failure evidence')
                self.assertEqual(manifest['newly_reviewed'], keys[10:])
                self.assertEqual(manifest['revised_reviews'], ['t00'])
                self.assertEqual(manifest['completed_new_audit14'], keys)
                for ref in manifest['new_review_prechecks'].values():
                    checked = io.read_json(root / ref['path'])
                    self.assertTrue(io.evidence_ok(checked['base_record'], root))
                    previous = io.read_json(root / checked['base_record']['path'])
                    self.assertEqual(previous['audit']['checker_version'], 'previous')
                note = io.read_json(root / manifest['revision_records']['t00']['path'])
                self.assertEqual((root / note['previous_record']['path']).read_bytes(), original_record)
                frozen = io.unique_index(io.read_jsonl(root / manifest['ledger']['path']), 'task_key')
                self.assertEqual(frozen['t00']['audit']['checker_version'], 'replacement')
                self.assertTrue(all(frozen[r['task_key']]['native'] == r['native'] for r in records))
                pool = io.unique_index(io.read_jsonl(root / manifest['candidates']['path']), 'task_key')
                self.assertFalse(pool['t00']['eligible_for_sampling'])
                self.assertTrue(pool['t10']['eligible_for_sampling'])
                self.assertEqual(io.read_json(review / 'state.json')['ready_revisions'], {})

    def test_revision_does_not_fill_tenth_new_task_slot(self):
        state = {'newly_completed_audit14': ['old'],
                 'ready_overlays': {f'new{i}': {} for i in range(9)}, 'ready_revisions': {'old': {}}}
        target = {'old'} | {f'new{i}' for i in range(20)}
        with self.assertRaises(ValueError): publication_selection(state, target)
        state['ready_overlays']['new9'] = {}
        new, revised = publication_selection(state, target)
        self.assertEqual((len(new), revised), (10, ['old']))
        self.assertEqual(len(set(state['newly_completed_audit14']) | set(new)), 11)

    def test_revisions_only_wait_until_new_task_queue_is_complete(self):
        state = {'newly_completed_audit14': ['old'], 'ready_overlays': {}, 'ready_revisions': {'old': {}}}
        with self.assertRaises(ValueError): publication_selection(state, {'old', 'unfinished'})
        self.assertEqual(publication_selection(state, {'old'}), ([], ['old']))
        state['ready_revisions']['unreviewed'] = {}
        with self.assertRaises(ValueError): publication_selection(state, {'old', 'unreviewed'})

    def test_revision_stage_archives_prior_record_without_changing_it(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); review = root / 'review'; review.mkdir()
            record = root / 'validation/t/record.json'
            io.write_json(record, {'task_key': 't', 'native': {'conclusion': 'weak'},
                                   'audit': {'checker_version': 'old', 'conclusion': 'validated'}})
            old_bytes = record.read_bytes()
            io.write_jsonl(root / 'catalog/tasks.jsonl', [{'task_key': 't', 'validation_ref': 'validation/t/record.json'}])
            io.write_json(review / 'state.json', {'newly_completed_audit14': ['t'], 'ready_overlays': {}})
            io.write_json(root / 'validation/delivery-status.json', {})
            overlay = root / 'new-overlay.json'; io.write_json(overlay, {'task_key': 't', 'checker_version': 'new'})
            check = {'task_key': 't', 'audit14_review_complete': True, 'conclusion': 'validated'}
            with patch('phase2_publish.ROOT', root), patch('phase2_publish.precheck', return_value=check):
                result = stage_revision(review, overlay, 'Actual legal configuration rejected by old checker')
                self.assertEqual(result['new_completed_task_credit'], 0)
                self.assertEqual(record.read_bytes(), old_bytes)
                state = io.read_json(review / 'state.json')
                self.assertEqual(state['newly_completed_audit14'], ['t'])
                ref = state['ready_revisions']['t']['previous-record']
                self.assertTrue(io.evidence_ok(ref, root))
                self.assertEqual((root / ref['path']).read_bytes(), old_bytes)
                with self.assertRaises(ValueError): stage_revision(review, overlay, 'duplicate')

    def test_dependency_correction_cannot_rewrite_evidence_or_scores(self):
        old = {'task_key': 't', 'checks': {'reference': {'status': 'gap'}},
               'code_refs': [{'path': 'old.py', 'sha256': 'old'}]}
        new = deepcopy(old); new['code_refs'].append({'path': 'extra.py', 'sha256': 'extra'})
        validate_dependency_extension(old, new)
        changed = deepcopy(new); changed['checks']['reference']['status'] = 'pass'
        with self.assertRaises(ValueError): validate_dependency_extension(old, changed)
        changed = deepcopy(new); changed['code_refs'][0]['sha256'] = 'different implementation'
        with self.assertRaises(ValueError): validate_dependency_extension(old, changed)
        changed = deepcopy(new); del changed['code_refs'][0]
        with self.assertRaises(ValueError): validate_dependency_extension(old, changed)

    def test_dependency_correction_preserves_original_and_rejects_published_task(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); review = root / 'review'; ready = review / 'ready/t'; ready.mkdir(parents=True)
            old = {'task_key': 't', 'checks': {'reference': {'status': 'gap'}},
                   'code_refs': [{'path': 'old.py', 'sha256': 'old'}]}
            new = deepcopy(old); new['code_refs'].append({'path': 'extra.py', 'sha256': 'extra'})
            precheck = {'audit14_review_complete': True, 'conclusion': 'pending', 'check_counts': {'gap': 14}}
            io.write_json(ready / 'overlay.json', old); io.write_json(ready / 'precheck.json', precheck)
            previous = {name: io.evidence_ref(ready / (name + '.json'), root) for name in ('overlay', 'precheck')}
            io.write_json(review / 'state.json', {'ready_overlays': {'t': previous}, 'newly_completed_audit14': []})
            io.write_json(root / 'validation/delivery-status.json', {})
            candidate = root / 'extension.json'; io.write_json(candidate, new)
            with patch('phase2_publish.ROOT', root), patch('phase2_publish.precheck', return_value=precheck):
                result = extend_staged_dependencies(review, candidate)
                self.assertEqual(result['audit_conclusion'], 'pending')
                self.assertTrue(all(io.evidence_ok(ref, root) for ref in previous.values()))
                state = io.read_json(review / 'state.json')
                self.assertNotEqual(state['ready_overlays']['t'], previous)
                correction = io.read_json(root / state['staged_dependency_amendments'][0]['path'])
                self.assertEqual(correction['previous'], previous)
                self.assertEqual(io.read_json(root / correction['current']['overlay']['path']), new)
                state['newly_completed_audit14'] = ['t']; io.write_json(review / 'state.json', state)
                with self.assertRaises(ValueError): extend_staged_dependencies(review, candidate)

    def test_premerge_contract_evidence_survives_canonical_contract_update(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); contract = root / 'contract.yaml'; archive = root / 'archive'; archive.mkdir()
            io.write_json(contract, {'native': 'unchanged', 'audit': 'old'})
            ref = io.evidence_ref(contract, root)
            overlay = {'checks': {'assets': {'status': 'pass', 'reason': 'Reviewed original contract', 'evidence': [ref]}}}
            original = deepcopy(overlay)
            def save(value):
                self.assertTrue(io.evidence_ok(value, root))
                dest = archive / value['sha256']; dest.write_bytes((root / value['path']).read_bytes())
                return io.evidence_ref(dest, root)
            fixed = archive_mutable_refs(overlay, [contract], save, root)
            io.write_json(contract, {'native': 'unchanged', 'audit': 'new'})
            self.assertEqual(_check(overlay['checks']['assets'], root)['status'], 'pending')
            self.assertEqual(_check(fixed['checks']['assets'], root)['status'], 'pass')
            self.assertEqual(overlay, original)

    def test_explicit_historical_alias_archives_old_bytes_not_current_contract(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); canonical = root / 'contract.json'; old = root / 'immutable.json'
            io.write_json(canonical, {'native': 'same', 'audit': 'old'})
            original = io.evidence_ref(canonical, root)
            old.write_bytes(canonical.read_bytes())
            io.write_json(canonical, {'native': 'same', 'audit': 'current'})
            value = {'history': {'original': original, 'immutable': io.evidence_ref(old, root)},
                     'current': io.evidence_ref(canonical, root)}
            before = deepcopy(value)
            def save(ref):
                if not io.evidence_ok(ref, root): raise ValueError('Changed input')
                target = root / 'objects' / ref['sha256']; target.parent.mkdir(exist_ok=True)
                target.write_bytes((root / ref['path']).read_bytes())
                return io.evidence_ref(target, root)
            fixed = archive_mutable_refs(value, [canonical], save, root)
            self.assertEqual(io.read_json(root / fixed['history']['original']['path'])['audit'], 'old')
            self.assertEqual(io.read_json(root / fixed['current']['path'])['audit'], 'current')
            self.assertEqual(value, before)

    def test_historical_alias_rejects_different_hash_and_corrupt_immutable_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); canonical = root / 'contract.json'; old = root / 'immutable.json'
            canonical.write_text('original'); original = io.evidence_ref(canonical, root)
            old.write_text('original'); immutable = io.evidence_ref(old, root)
            canonical.write_text('current')
            forged = {'original': original, 'immutable': {**immutable, 'sha256': '0' * 64}}
            with self.assertRaises(ValueError):
                archive_mutable_refs(forged, [canonical], lambda r: r, root)
            old.write_text('corrupted')
            with self.assertRaises(ValueError):
                archive_mutable_refs({'original': original, 'immutable': immutable},
                                     [canonical], lambda r: r, root)

    def test_no_implicit_history_search_for_stale_reference(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); canonical = root / 'contract.json'; old = root / 'old.json'
            canonical.write_text('old'); ref = io.evidence_ref(canonical, root)
            old.write_text('old'); canonical.write_text('new')
            def save(value):
                if not io.evidence_ok(value, root): raise ValueError('Changed input')
                return value
            with self.assertRaises(ValueError):
                archive_mutable_refs({'evidence': ref, 'unrelated': io.evidence_ref(old, root)},
                                     [canonical], save, root)

    def test_merge_refuses_silent_downgrade_even_if_overall_pending_is_unchanged(self):
        overlay = {'checks': {'assets': {'status': 'pass', 'reason': 'Bound evidence'}}}
        record = {'task_key': 'synthetic', 'audit': {'conclusion': 'pending', 'checks': {'assets': {'status': 'pending'}}}}
        with self.assertRaises(ValueError): assert_audit_preserved(record, overlay, {'conclusion': 'pending'})

    def test_merge_keeps_native_and_native_requirement_coverage_exact(self):
        contract = {'task_key': 'synthetic', 'checks': {'reference': {'status': 'gap'}},
            'requirements': [{'id': 'R1', 'coverage': {'native': {'status': 'missed_wrong', 'evidence': ['unchanged']},
                                                      'audit': {'status': 'pending'}}}],
            'audit': {'checker_version': 'old-audit'}}
        original = deepcopy(contract)
        overlay = {name: name for name in ('checker_version', 'review_status', 'reference_kind', 'witness_kind', 'scope')}
        overlay.update(code_refs=[], checks={'reference': {'status': 'pass'}},
            requirement_coverage={'R1': {'status': 'covered', 'evidence': ['new-audit']}},
            base_identity={'contract': 'old', 'task': 'same-task'})
        merged = merge_audit(contract, overlay)
        self.assertEqual(contract, original)
        self.assertEqual(merged['checks'], original['checks'])
        self.assertEqual(merged['requirements'], original['requirements'])
        self.assertEqual(merged['audit']['requirements'][0]['coverage']['native'],
                         original['requirements'][0]['coverage']['native'])
        self.assertEqual(merged['audit']['reviewed_inputs'], {'task': 'same-task'})

    def test_less_than_ten_cannot_publish_or_claim_final_remainder(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); review = root / 'review'; review.mkdir()
            io.write_json(root / 'inventory.json', {'tasks': [{'task_key': f't{i}', 'audit_status': 'pending'} for i in range(20)]})
            io.write_json(review / 'state.json', {'inventory': {'path': 'inventory.json'},
                'newly_completed_audit14': [], 'ready_overlays': {f't{i}': {} for i in range(3)}})
            with patch('phase2_publish.ROOT', root):
                with self.assertRaises(ValueError): publish(review)
                with self.assertRaises(ValueError): publish(review, final_remainder=True)

    def test_rehashed_native_promotion_in_frozen_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); pool = root / 'pool'; pool.mkdir()
            native = {'conclusion': 'weak', 'checks': {'negative': {'status': 'fail'}}}
            base = [{'task_key': 'synthetic', 'native': native, 'audit': {'conclusion': 'pending'}, 'sampling_eligible': False}]
            after = deepcopy(base); after[0]['audit']['conclusion'] = 'validated'
            after[0]['audit']['checks'] = {name: {'status': 'pass'} for name in CHECKS}
            after[0]['sampling_eligible'] = True
            io.write_jsonl(root / 'baseline.jsonl', base); io.write_jsonl(root / 'ledger.jsonl', after)
            io.write_jsonl(root / 'catalog.jsonl', [{'task_key': 'synthetic', 'split': 'test'}])
            candidates = [{'task_key': 'synthetic', 'split': 'test', 'native': 'weak',
                           'audit': 'validated', 'eligible_for_sampling': False}]
            io.write_jsonl(root / 'candidates.jsonl', candidates)
            (root / 'report.md').write_text('Synthetic freeze verification fixture')
            m = {'baseline_ledger': io.evidence_ref(root / 'baseline.jsonl', root),
                 'ledger': io.evidence_ref(root / 'ledger.jsonl', root),
                 'candidates': io.evidence_ref(root / 'candidates.jsonl', root), 'report': io.evidence_ref(root / 'report.md', root),
                 'catalog': io.evidence_ref(root / 'catalog.jsonl', root),
                 'split': io.evidence_ref(root / 'report.md', root), 'source_lock': io.evidence_ref(root / 'report.md', root),
                 'archived_review_inputs': [], 'audit_counts': {'validated': 1, 'pending': 0}, 'native_counts': {'weak': 1},
                 'completed_new_audit14': ['synthetic']}
            io.write_json(pool / 'manifest.json', {**m, 'hash': io.digest(m)})
            with patch('phase2_publish.ROOT', root): self.assertTrue(verify_freeze(pool)['passed'])
            after[0]['audit']['checks']['assets']['status'] = 'pending'
            io.write_jsonl(root / 'ledger.jsonl', after); m['ledger'] = io.evidence_ref(root / 'ledger.jsonl', root)
            io.write_json(pool / 'manifest.json', {**m, 'hash': io.digest(m)})
            with patch('phase2_publish.ROOT', root), self.assertRaises(ValueError): verify_freeze(pool)
            after[0]['audit']['checks']['assets']['status'] = 'pass'
            io.write_jsonl(root / 'ledger.jsonl', after); m['ledger'] = io.evidence_ref(root / 'ledger.jsonl', root)
            candidates[0]['eligible_for_sampling'] = True
            io.write_jsonl(root / 'candidates.jsonl', candidates); m['candidates'] = io.evidence_ref(root / 'candidates.jsonl', root)
            io.write_json(pool / 'manifest.json', {**m, 'hash': io.digest(m)})
            with patch('phase2_publish.ROOT', root), self.assertRaises(ValueError): verify_freeze(pool)
            candidates[0]['eligible_for_sampling'] = False
            io.write_jsonl(root / 'candidates.jsonl', candidates); m['candidates'] = io.evidence_ref(root / 'candidates.jsonl', root)
            after[0]['native']['conclusion'] = 'validated'
            io.write_jsonl(root / 'ledger.jsonl', after); m['ledger'] = io.evidence_ref(root / 'ledger.jsonl', root)
            io.write_json(pool / 'manifest.json', {**m, 'hash': io.digest(m)})
            with patch('phase2_publish.ROOT', root), self.assertRaises(ValueError): verify_freeze(pool)


if __name__ == '__main__': unittest.main()
