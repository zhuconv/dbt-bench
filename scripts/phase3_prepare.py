"""Prepare train-only blind reconstructions, preserving prior task/evidence bytes."""
from dbt_bench.local_config import expand as _release_expand
import argparse
import difflib
from pathlib import Path
import shutil
from dbt_bench import io
from prepare_audit_rebuild import prepare as prepare_rebuild
from review_tools import ROOT
PILOT = Path(_release_expand('${SCRATCH_ROOT}/claude-two-version-pilot'))
FORMAL = {'agree', 'native_only', 'patched_pass'}
GOLD_DERIVED = {'spider2__hive001', 'ade__ade-bench-f1010-medium'}

def task_key(name):
    return ('ade__' if name.startswith('ade-bench-') else 'deb__' if name.startswith('dbt-') else 'spider2__') + name

def plan_entry(entry, catalog, results, pilot=PILOT, root=ROOT):
    if 'mode' in entry and entry['mode'] != entry.get('rebuild', {}).get('mode', 'rebuild'):
        raise ValueError('Execution mode belongs inside rebuild; refuse silently ignored top-level mode')
    key = entry['task_key']
    row = catalog[key]
    if row['split'] != 'train':
        raise ValueError('Phase 3 cannot modify or execute a test task: ' + key)
    name = row['upstream_task_id']
    if task_key(name) != key:
        raise ValueError('Ambiguous pilot task mapping')
    result = results[name]
    category = result['outcome']
    if category not in FORMAL:
        raise ValueError('Excluded pilot task cannot be executed: ' + key)
    original = pilot / name / 'instruction.md'
    canonical = root / row['run_path'] / 'instruction.md'
    if original.read_bytes() != canonical.read_bytes():
        raise ValueError('Blind author saw a different original instruction: ' + key)
    directory = pilot / (name + '_v2' if category == 'patched_pass' else name)
    selected = directory / 'instruction.md'
    if not selected.is_file():
        raise ValueError('Selected instruction unavailable: ' + key)
    meta = entry.get('phase3', {})
    for field, path in [('original_instruction', original), ('selected_instruction', selected)]:
        if meta.get(field) and meta[field] != io.evidence_ref(path, root):
            raise ValueError('Request instruction does not match phase 3 source: ' + key)
    variant = category == 'patched_pass'
    identity = {'schema_version': 1, 'task_key': key, 'split': 'train', 'category': category, 'original_instruction': io.evidence_ref(original, root), 'selected_instruction': io.evidence_ref(selected, root), 'base_record': io.evidence_ref(root / row['validation_ref'], root), 'canonical_task_hash': io.tree_hash(root / row['run_path']), 'task_variant': variant, 'gold_derived_correction': key in GOLD_DERIVED, 'patch_type': 'task_instruction_variant' if variant else None, 'ungraded_differences_reported': result.get('unchecked_diff', ''), 'external_result': result, 'external_result_scope': 'Lead only; no reward imported', 'model_requests': 0}
    identity['task_version'] = 'phase3-instruction-' + identity['selected_instruction']['sha256'][:16] if variant else 'original-' + identity['canonical_task_hash'][:16]
    return identity

def prepare(base_batch, requests_path, output, concurrent=2, pilot=PILOT, root=ROOT):
    output, root = (Path(output), Path(root))
    requested = io.read_json(requests_path)
    rows = io.unique_index(io.read_jsonl(root / 'catalog/tasks.jsonl'), 'task_key')
    results = io.read_json(pilot / 'results.json')
    plans = {e['label']: plan_entry(e, rows, results, pilot, root) for e in requested['tasks']}
    result = prepare_rebuild(base_batch, requests_path, output, concurrent)
    manifest = io.read_json(output / 'manifest.json')
    shutil.copyfile(Path(__file__), output / 'phase3-preparation-source.py')
    for label, identity in plans.items():
        target = output / 'tasks' / label
        before = io.file_hash(target / 'instruction.md')
        if before != identity['original_instruction']['sha256']:
            raise ValueError('Mechanical base instruction differs: ' + label)
        if identity['task_variant']:
            old = (target / 'instruction.md').read_text()
            shutil.copyfile(root / identity['selected_instruction']['path'], target / 'instruction.md')
            patch = output / 'instruction-patches' / (label + '.diff')
            patch.parent.mkdir(exist_ok=True)
            patch.write_text(''.join(difflib.unified_diff(old.splitlines(True), (target / 'instruction.md').read_text().splitlines(True), fromfile='original/instruction.md', tofile='variant/instruction.md')))
            identity['instruction_patch'] = io.evidence_ref(patch, root)
        entry = manifest['tasks'][label]
        if io.file_hash(target / 'tests/mechanical-native-test.sh') != entry['native_verifier']['sha256']:
            raise ValueError('Native verifier changed')
        identity['instruction_before_sha256'] = before
        identity['instruction_after_sha256'] = io.file_hash(target / 'instruction.md')
        identity['native_verifier'] = entry['native_verifier']
        entry.update(phase3=identity, validation_copy_hash=io.tree_hash(target))
    manifest.update(phase=3, phase3_source=io.evidence_ref(output / 'phase3-preparation-source.py', root), pilot_results=io.evidence_ref(pilot / 'results.json', root))
    io.write_json(output / 'manifest.json', manifest)
    return {**result, 'phase': 3, 'new_instruction_versions': sum((p['task_variant'] for p in plans.values()))}
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-batch', type=Path, required=True)
    parser.add_argument('--requests', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--concurrent', type=int, default=2)
    args = parser.parse_args()
    print(prepare(args.base_batch, args.requests, args.output, args.concurrent))
