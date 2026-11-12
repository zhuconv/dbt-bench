"""Behavioral regressions for immutable completion and phase-1 root merging."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_batch_completion import missing_slots, validate_completion_config
from phase1_publish import check_overlay, merge_requirements, frozen_ref_ok
from review_tools import cached_hashes
from dbt_bench import io
from mechanical_compare import compare_rows
from mechanical_observer import encode
from decimal import Decimal
from prepare_mechanical_cases import variants, observation_spec
from phase1_adjudicate import check_binding
from evidence_archive import archive_review_refs


class AdjudicationTests(unittest.TestCase):
    def test_native_archive_preserves_existing_audit_and_rule_audit_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'validation/deb/task/checker.py'
            source.parent.mkdir(parents=True)
            source.write_text('existing checker evidence')
            ref = io.evidence_ref(source, root)
            audit = {'checker_version': 'v1', 'checks': {'assets': {'evidence': [ref]}}}
            value = {'evidence': [ref], 'audit': audit,
                     'requirements': [{'coverage': {'native': {'evidence': [ref]},
                                                    'audit': {'evidence': [ref]}}}]}
            before = deepcopy(value)
            result = archive_review_refs(value, root, root / 'objects', preserve_audit=True)
            self.assertEqual(value, before)
            self.assertEqual(result['audit'], before['audit'])
            coverage = result['requirements'][0]['coverage']
            self.assertEqual(coverage['audit'], before['requirements'][0]['coverage']['audit'])
            self.assertNotEqual(result['evidence'][0]['path'], ref['path'])
            self.assertEqual(coverage['native']['evidence'], result['evidence'])
            source.write_text('changed working file')
            self.assertTrue(io.evidence_ok(result['evidence'][0], root))
            self.assertFalse(io.evidence_ok(result['audit']['checks']['assets']['evidence'][0], root))

    def test_published_evidence_keeps_original_bytes_after_working_review_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'phase1-review-v1/semantic.json'
            source.parent.mkdir()
            source.write_text('reviewed original')
            original = io.evidence_ref(source, root)
            archived = archive_review_refs({'evidence': [original]}, root, root / 'objects')
            source.write_text('later working review')
            recovered = archive_review_refs({'evidence': [original]}, root, root / 'objects')
            self.assertEqual(recovered, archived)
            self.assertTrue(io.evidence_ok(recovered['evidence'][0], root))
            self.assertEqual((root / recovered['evidence'][0]['path']).read_text(), 'reviewed original')
            missing = {**original, 'sha256': 'f' * 64}
            with self.assertRaises(AssertionError):
                archive_review_refs(missing, root, root / 'objects')

    def test_correction_requires_same_trial_task_proof_and_accepted_error(self):
        proof_ref = {'path': '/tmp/a/agent/case-proof.json', 'sha256': 'proof'}
        correction = {'independent_proof': proof_ref, 'checker_evidence': {'sha256': 'result'},
                      'case_validity': 'business_error'}
        record = {'task_key': 'one', 'kind': 'negative', 'result': {'sha256': 'result'},
                  'artifacts': [proof_ref], 'proof_valid_for_declared_scope': True,
                  'proof_completed_before_verifier': True, 'native_rewards': {'reward': 1.0}}
        proof = {'validity': 'business_error', 'tested_checker_invoked_to_define_validity': False}
        check_binding('one', correction, {'records': [record]}, proof)
        for change in ({'task_key': 'other'}, {'proof_completed_before_verifier': None},
                       {'native_rewards': {'reward': 0.0}}, {'kind': 'reference_relative'},
                       {'artifacts': [{'path': '/tmp/other/proof.json', 'sha256': 'proof'}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_binding('one', correction, {'records': [{**record, **change}]}, proof)
        with self.assertRaises(ValueError):
            check_binding('one', correction, {'records': [record, record]}, proof)
        with self.assertRaises(ValueError):
            check_binding('one', correction, {'records': [record]}, {**proof, 'tested_checker_invoked_to_define_validity': True})


class CaseBatchTests(unittest.TestCase):
    def test_case_observation_preserves_outputs_and_limits_file_capture(self):
        base = {'files': ['/app/project/models/a.sql'], 'outputs': [{'relation': 'main.result'}]}
        result = observation_spec(base, {'observation_files': ['/app/project/models/a.sql', '/root/.dbt/profiles.yml'],
                                         'observation_sql_types': True})
        self.assertEqual(base['files'], ['/app/project/models/a.sql'])
        self.assertEqual(result['outputs'], base['outputs'])
        self.assertEqual(result['files'], ['/app/project/models/a.sql', '/root/.dbt/profiles.yml'])
        for path in ('/app', '/app/**/*', 'relative.sql', '/app/../private'):
            with self.subTest(path=path), self.assertRaises(AssertionError):
                observation_spec(base, {'observation_files': [path]})

    def test_reference_relative_experiment_keeps_distinct_kind(self):
        entry = {'reference_relative': {'baseline_correctness': 'unresolved'}}
        self.assertEqual(variants(entry)[0][0], 'reference_relative')
        self.assertEqual(variants(entry, ['equivalent']), [])
        with self.assertRaises(AssertionError):
            variants({'variants': [{'kind': 'equivalent', 'label': 'misclassified', 'spec': {},
                                    'expected_validity': 'reference_relative_equivalent'}]})

    def test_unconstructed_case_is_not_scheduled(self):
        self.assertEqual(variants({'equivalent': {'case_id': 'lawful'}, 'negative': None}),
                         [('equivalent', 'equivalent', {'case_id': 'lawful'})])

    def test_distinct_rule_cases_survive_same_task_and_kind(self):
        entry = {'variants': [{'kind': 'negative', 'label': label, 'spec': {'rule': label}}
                              for label in ('wrong_key', 'missing_row')]}
        self.assertEqual([r[1] for r in variants(entry)], ['wrong_key', 'missing_row'])
        self.assertEqual(variants(entry, ['equivalent']), [])

    def test_duplicate_and_unsafe_case_paths_are_rejected(self):
        for labels in (['same', 'same'], ['../outside'], ['/absolute']):
            entry = {'variants': [{'kind': 'negative', 'label': label, 'spec': {}} for label in labels]}
            with self.subTest(labels=labels), self.assertRaises(AssertionError):
                variants(entry)


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.config = {'agents': [{'name': 'oracle'}], 'n_attempts': 2,
                       'tasks': [{'path': '/a'}, {'path': '/b'}]}

    def result(self, identifier, path='/a', **extra):
        return {'id': identifier, 'finished_at': '2026-11-06T00:00:00Z',
                'config': {'task': {'path': path}, 'agent': {'name': 'oracle'}}, **extra}

    def test_failed_terminal_trial_is_never_retried_implicitly(self):
        result = self.result('one', exception_info={'exception_type': 'RewardFileNotFoundError'},
                             verifier_result=None)
        self.assertEqual(missing_slots(self.config, [result]), {'/a': 1, '/b': 2})

    def test_unknown_duplicate_excess_and_nonterminal_fail_closed(self):
        cases = [[self.result('one', '/unknown')], [self.result('one'), self.result('one')],
                 [self.result(str(i)) for i in range(3)], [self.result('one', finished_at=None)]]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                missing_slots(self.config, values)

    def test_model_agent_and_ambiguous_plan_rejected(self):
        cfg = deepcopy(self.config)
        cfg['agents'][0]['name'] = 'qwen-code'
        with self.assertRaises(ValueError):
            missing_slots(cfg, [])
        cfg = deepcopy(self.config)
        cfg['tasks'].append({'path': '/a'})
        with self.assertRaises(ValueError):
            missing_slots(cfg, [])

    def test_success_and_failure_both_fill_slots(self):
        values = [self.result('a1'), self.result('a2'), self.result('b1', '/b'),
                  self.result('b2', '/b', exception_info={'exception_type': 'AgentTimeoutError'})]
        self.assertEqual(missing_slots(self.config, values), {})

    def test_locked_no_model_cases_resume_only_missing_terminal_slots(self):
        cfg = deepcopy(self.config)
        agent = {'name': None, 'import_path': 'native_mechanical_agent:NativeMechanicalAgent',
                 'model_name': None}
        cfg['agents'] = [agent]
        result = self.result('retained', exception_info={'exception_type': 'RewardFileNotFoundError'})
        result['config']['agent'] = deepcopy(agent)
        self.assertEqual(missing_slots(cfg, [result]), {'/a': 1, '/b': 2})
        for change in ({'model_name': 'a-model'}, {'base_url': 'https://model.invalid'},
                       {'import_path': 'unknown_agent:Agent'}, {'name': 'qwen-code'}):
            invalid = deepcopy(cfg)
            invalid['agents'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                missing_slots(invalid, [])

    def test_completion_preserves_task_and_execution_identity(self):
        cfg = {**self.config, 'environment': {'type': 'singularity'}, 'retry': {'max_retries': 0}}
        resumed = {**cfg, 'tasks': cfg['tasks'][:1], 'n_attempts': 1,
                   'job_name': 'recovery', 'jobs_dir': '/new/jobs'}
        validate_completion_config(cfg, resumed)
        for change in ({'environment': {'type': 'docker'}}, {'retry': {'max_retries': 3}},
                       {'tasks': [{'path': '/a', 'git_commit_id': 'other'}]}, {'n_attempts': 2}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_completion_config(cfg, {**resumed, **change})


class ReviewTests(unittest.TestCase):
    def test_frozen_reference_survives_working_file_change_only_with_exact_archive(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'working.json'
            p.write_text('frozen bytes')
            ref = io.evidence_ref(p, Path.cwd())
            archive = Path(d) / 'archive'
            archive.mkdir()
            (archive / ref['sha256']).write_bytes(p.read_bytes())
            p.write_text('later review')
            self.assertTrue(frozen_ref_ok(ref, archive))
            (archive / ref['sha256']).write_text('corrupt archive')
            self.assertFalse(frozen_ref_ok(ref, archive))

    def test_changed_file_invalidates_in_process_hash_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'evidence.txt'
            p.write_text('original')
            original_function = io.file_hash
            with cached_hashes():
                first = io.file_hash(p)
                p.write_text('modified')  # same byte length
                self.assertNotEqual(io.file_hash(p), first)
                p.unlink()
                with self.assertRaises(FileNotFoundError):
                    io.file_hash(p)
            self.assertIs(io.file_hash, original_function)

    def test_gap_requires_real_evidence_and_matching_identity(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'inspection.json'
            p.write_text('{"searched":[],"missing":"real build witness"}')
            value = {'task_key': 'synthetic', 'target': 'native', 'base_identity': {'task': 'one'},
                     'checks': {'reference': {'status': 'gap', 'reason': 'No witness in inspected sources',
                                             'evidence': [io.evidence_ref(p, Path.cwd())]}}}
            check_overlay(value, {'reference'}, {'task': 'one'})
            with self.assertRaises(ValueError):
                check_overlay(value, {'reference'}, {'task': 'two'})
            p.write_text('{}')
            with self.assertRaises(ValueError):
                check_overlay(value, {'reference'}, {'task': 'one'})

    def test_semantic_merge_cannot_erase_confirmed_native_miss_or_audit(self):
        native = {'status': 'missed_wrong', 'independent_proof': {'path': 'proof'},
                  'checker_evidence': {'path': 'result'}, 'case_validity': 'business_error'}
        old = [{'id': 'R1', 'coverage': {'native': native, 'audit': {'status': 'covered'}}}]
        new = [{'id': 'R1', 'coverage': {'native': {'status': 'gap'}}}, {'id': 'R2', 'coverage': {}}]
        merged = merge_requirements(old, new)
        self.assertEqual(merged[0]['coverage']['native'], native)
        self.assertEqual(merged[0]['coverage']['audit'], {'status': 'covered'})
        self.assertEqual(len(merged), 2)
        self.assertEqual(old[0]['coverage']['native'], native)

    def test_unknown_reference_kind_is_rejected_before_contract_write(self):
        value = {'task_key': 'synthetic', 'target': 'native', 'base_identity': {},
                 'checks': {}, 'contract_updates': {'reference_kind': 'reference_sql'}}
        with self.assertRaisesRegex(ValueError, 'Unknown reference_kind'):
            check_overlay(value, set(), {})
        value['contract_updates'] = {'witness_kind': 'oracle_passes'}
        with self.assertRaisesRegex(ValueError, 'Unknown witness_kind'):
            check_overlay(value, set(), {})
        value['contract_updates'] = {'reference_kind': 'aggregate_assertions', 'witness_kind': 'unknown'}
        check_overlay(value, set(), {})

    def test_repeated_requirement_ids_rejected(self):
        with self.assertRaises(ValueError):
            merge_requirements([], [{'id': 'same'}, {'id': 'same'}])

    def test_overlay_preflight_rejects_extra_contract_fields(self):
        value = {'task_key': 'synthetic', 'target': 'native', 'base_identity': {},
                 'checks': {}, 'contract_updates': {'review_completion_scope': 'Explanation'}}
        with self.assertRaisesRegex(ValueError, 'Unsupported contract updates'):
            check_overlay(value, set(), {})
        value['contract_updates'] = {'audit': {'conclusion': 'validated'}}
        with self.assertRaisesRegex(ValueError, 'Unsupported contract updates'):
            check_overlay(value, set(), {})
        value['contract_updates'] = {'notes': ['Explanation']}
        check_overlay(value, set(), {})


class SnapshotTests(unittest.TestCase):
    def test_integer_identity_and_decimal_scale_are_not_floated(self):
        a = [[9007199254740992, encode(Decimal('0.00000000000000001'))]]
        b = [[9007199254740993, encode(Decimal('0.00000000000000001'))]]
        self.assertFalse(compare_rows(a, b, ['id', 'amount'])['equal'])
        self.assertEqual(encode(Decimal('999999999999999999.99'))['value'], '999999999999999999.99')

    def test_same_column_bags_cannot_hide_wrong_associations(self):
        result = compare_rows([[1, 'A'], [2, 'B']], [[1, 'B'], [2, 'A']], ['id', 'name'])
        self.assertFalse(result['equal'])
        self.assertTrue(all(result['column_multisets'].values()))

    def test_row_reordering_allowed_but_duplicates_are_preserved(self):
        rows = [[1, 'A'], [2, 'B']]
        self.assertTrue(compare_rows(rows, list(reversed(rows)), ['id', 'name'])['equal'])
        self.assertFalse(compare_rows(rows, [rows[0], rows[0]], ['id', 'name'])['equal'])
        self.assertFalse(compare_rows(rows, list(reversed(rows)), ['id', 'name'], ordered=True)['equal'])

    def test_dynamic_columns_are_validated_not_dropped(self):
        a = [[1, {'$type': 'datetime', 'value': '2026-11-06T10:00:01+00:00'}]]
        b = [[1, {'$type': 'datetime', 'value': '2026-11-06T10:01:01+00:00'}]]
        windows = [['2026-11-06T10:00:00Z', '2026-11-06T10:00:02Z'],
                   ['2026-11-06T10:01:00Z', '2026-11-06T10:01:02Z']]
        dynamic = {'updated_at': {'kind': 'current_timestamp'}}
        self.assertTrue(compare_rows(a, b, ['id', 'updated_at'], dynamic=dynamic, windows=windows)['equal'])
        b[0][1]['value'] = '1900-01-01T00:00:00+00:00'
        self.assertFalse(compare_rows(a, b, ['id', 'updated_at'], dynamic=dynamic, windows=windows)['equal'])
        self.assertIsNone(compare_rows(a, b, ['id', 'updated_at'], dynamic=dynamic)['equal'])
