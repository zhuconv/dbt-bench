import importlib.util
import json
import os
from pathlib import Path
import pickle
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/visibility_scan.py'
SPEC = importlib.util.spec_from_file_location('visibility_scan_tested', PATH)
scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scan)


class VisibilityScanTests(unittest.TestCase):
    def test_declared_target_is_used_for_structured_cache_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            path.write_text(json.dumps({'nodes': {'model.workday.workday__job_overview': {'raw_code': 'select 1'}}}))
            spec = {'hidden_assets': [], 'target_relation': 'workday__job_overview',
                    'structured_artifacts': [{'path': str(path), 'kind': 'json'}]}
            observed = scan.inspect_artifacts(spec)
            self.assertEqual(len(observed[0]['result']['target_mentions']), 1)
            self.assertFalse(observed[0].get('error'))
            del spec['target_relation']
            self.assertEqual(scan.inspect_artifacts(spec)[0]['result']['target_mentions'], [])

    def inspect(self, root, data, maximum=2048):
        return scan.scan({'hidden_assets': [scan.fingerprint('hidden', data)],
                          'maximum_text_bytes': maximum}, [str(root)], root / 'report')

    def test_embedded_source_survives_comments_and_whitespace_changes(self):
        hidden = b'select channel,\n sum(credit) as total\nfrom attribution\ngroup by channel\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'wrapped.sh').write_text('cat << EOF\n-- extra comment\n select   channel,\n sum(credit)   as total\nfrom attribution\ngroup by channel\nEOF\n')
            result = self.inspect(root, hidden)
            self.assertEqual([m['kind'] for m in result['matches']], ['contiguous_normalized_source'])
            self.assertEqual(result['errors'], [])

    def test_reordered_partial_lines_are_not_a_whole_source_match(self):
        hidden = b'first line\nsecond line\nthird line\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'near-match').write_bytes(b'first line\nthird line\nsecond line\n')
            self.assertEqual(self.inspect(root, hidden)['matches'], [])

    def test_large_exact_hidden_file_is_checked_despite_text_limit(self):
        hidden = b'large exact material' * 20
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'large').write_bytes(hidden)
            result = self.inspect(root, hidden, maximum=8)
            self.assertEqual([m['kind'] for m in result['matches']], ['exact_bytes'])
            self.assertEqual(result['counts']['larger_than_text_limit'], 1)

    def test_external_symlink_is_disclosed_and_not_claimed_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            outside = parent / 'outside'
            outside.write_bytes(b'hidden material')
            root = parent / 'root'
            root.mkdir()
            (root / 'alias').symlink_to(outside)
            result = self.inspect(root, outside.read_bytes())
            self.assertEqual(result['counts']['regular_files'], 0)
            self.assertEqual(result['symlinks'], [{'path': str(root / 'alias'), 'target': str(outside)}])

    def test_binary_exact_match_does_not_require_utf8(self):
        hidden = b'\x00\xffhidden database fixture\x00'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'fixture.bin').write_bytes(hidden)
            result = self.inspect(root, hidden)
            self.assertEqual([m['kind'] for m in result['matches']], ['exact_bytes'])
            self.assertEqual(result['counts']['binary_or_non_utf8'], 1)

    def test_json_escaped_compiled_source_is_decoded_before_matching(self):
        hidden = b'select channel,\nsum(credit) as total\nfrom attribution\n'
        data = json.dumps({'nodes': {'model': {'raw_code': hidden.decode()}}}).encode()
        self.assertFalse(scan.contains_lines(scan.source_lines(data), scan.source_lines(hidden)))
        result = scan.inspect_structure(data, 'json', [scan.fingerprint('gold-model', hidden)], 'attribution_report')
        self.assertEqual({m['kind'] for m in result['matches']}, {'exact_decoded_value', 'normalized_decoded_source'})

    def test_pickle_metadata_is_inspected_without_running_reduce(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'must-not-exist'
            class Payload:
                def __reduce__(self):
                    return os.system, ('touch ' + str(marker),)
            data = pickle.dumps({'model': 'model.project.attribution_report', 'payload': Payload()})
            result = scan.inspect_structure(data, 'pickle-opcodes', [], 'attribution_report')
            self.assertFalse(marker.exists())
            self.assertEqual(len(result['target_mentions']), 1)
