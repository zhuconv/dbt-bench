#!/usr/bin/env python3
"""Index the authorized DEB test pilot without editing any historical split."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path

from dbt_bench import io
from phase4_prepare import PILOT, FORMAL, task_key
from review_tools import ROOT

BROKEN = {'deb__dbt-fix-marketing-attribution', 'deb__dbt-product-return-rate-analysis'}
COUNTS = {'agree': 27, 'patched_pass': 10, 'gold_error': 2, 'degenerate_empty': 1}


def selected_directory(pilot, name, category):
    """Resolve the actual v2 spelling explicitly; never fall back silently."""
    pilot = Path(pilot)
    if category != 'patched_pass':
        return pilot / name
    found = [pilot / (name + suffix) for suffix in ('_v2', '_V2')
             if (pilot / (name + suffix) / 'instruction.md').is_file()]
    if len(found) != 1:
        raise ValueError('Require exactly one explicit v2 instruction directory: ' + name)
    return found[0]


def initial_candidates(base, keys):
    rows = deepcopy(base)
    indexed = io.unique_index(rows, 'task_key')
    for key in keys:
        row = indexed[key]
        if row['split'] != 'test' or row['source'] != 'deb':
            raise ValueError('Phase4 selection is limited to existing DEB test tasks')
        row.update(eligible_for_sampling=False,
            phase4_selection_reason='Awaiting current blind reconstruction/native14/audit14, or excluded by user triage.')
    assert [r for r in rows if r['split'] == 'train'] == [r for r in base if r['split'] == 'train']
    return rows


def initialize(output, root=ROOT, pilot=PILOT):
    output, root, pilot = map(Path, (output, root, pilot))
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Use a new SCRATCH phase4 review directory')
    rows = io.unique_index(io.read_jsonl(root / 'catalog/tasks.jsonl'), 'task_key')
    results = io.read_json(pilot / 'results_test.json')
    keys = {task_key(n) for n in results}
    wanted = {k for k, r in rows.items() if r['source'] == 'deb' and r['split'] == 'test'} - BROKEN
    if keys != wanted or len(keys) != 40 or Counter(v['outcome'] for v in results.values()) != COUNTS:
        raise ValueError('Pilot differs from authorized 40-task/category inventory')
    broken = []
    for key in sorted(BROKEN):
        row = rows[key]
        record = io.read_json(root / row['validation_ref'])
        if row['split'] != 'test' or record['native']['conclusion'] != 'broken':
            raise ValueError('Excluded native-broken status differs: ' + key)
        broken.append({'task_key': key, 'record': io.evidence_ref(root / row['validation_ref'], root)})
    tasks = []
    for name, external in sorted(results.items()):
        key = task_key(name); row = rows[key]; category = external['outcome']
        original = pilot / name / 'instruction.md'
        if original.read_bytes() != (root / row['run_path'] / 'instruction.md').read_bytes():
            raise ValueError('Original pilot instruction drift: ' + key)
        selected = selected_directory(pilot, name, category)
        refs = {n.replace('.', '_'): io.evidence_ref(selected / n, root)
                for n in ('instruction.md', 'decisions.md', 'build.txt') if (selected / n).is_file()}
        if category in FORMAL and set(refs) != {'instruction_md', 'decisions_md', 'build_txt'}:
            raise ValueError('Formal pilot source is incomplete: ' + key)
        triage = pilot / name / 'triage/triage.md'
        if triage.is_file(): refs['triage'] = io.evidence_ref(triage, root)
        if category == 'gold_error' and not triage.is_file():
            raise ValueError('Gold-error triage missing: ' + key)
        tasks.append({'task_key': key, 'source': 'deb', 'split': 'test',
            'category': category, 'formal_execution_required': category in FORMAL,
            'selected_inputs': refs, 'external_result': external,
            'selected_directory': str(selected), 'instruction_variant': category == 'patched_pass',
            'directory_spelling': selected.name,
            'directory_spelling_note': 'User referred to _V2; the exact existing directory is recorded here without fallback.' if category == 'patched_pass' else None,
            'gold_derived_correction': False,
            'previous_record': io.evidence_ref(root / row['validation_ref'], root),
            'status': 'pending' if category in FORMAL else 'excluded_pending_record_review',
            'scope': 'External blind result is a lead, not native/audit certification.'})
    baseline_ref = io.read_json(root / 'catalog/phase3-current.json')['selection']
    if not io.evidence_ok(baseline_ref, root): raise ValueError('Phase3 baseline changed')
    baseline = io.read_json(root / baseline_ref['path'])
    if baseline['version'] != 'train-phase3-v06': raise ValueError('Expected frozen train-phase3-v06 baseline')
    candidates = initial_candidates(io.read_jsonl(root / baseline['candidates']['path']), keys | BROKEN)
    protected = [root / n for n in ('catalog/tasks.jsonl', 'catalog/splits.json', 'catalog/phase3-current.json',
                 'catalog/phase3-task-versions.json', 'catalog/phase3-environment-versions.json',
                 'validation/ledger.jsonl', 'validation/delivery-status.json', 'validation/phase3-delivery-status.json')]
    for row in rows.values():
        record = root / row['validation_ref']; protected.extend([record, record.parent / 'contract.yaml'])
    protected.extend(p for p in (root / baseline_ref['path']).parent.iterdir() if p.is_file())
    output.mkdir(parents=True)
    io.write_json(output / 'protected-files.json', [io.evidence_ref(p, root) for p in protected])
    io.write_json(output / 'inventory.json', {'schema_version': 1, 'phase': 4, 'model_requests': 0,
        'pilot_results': io.evidence_ref(pilot / 'results_test.json', root), 'baseline_pool': baseline_ref,
        'counts': COUNTS, 'tasks': tasks, 'formal_tasks': 37, 'excluded_native_broken': broken,
        'historical_split': io.evidence_ref(root / 'catalog/splits.json', root),
        'protected_files': io.evidence_ref(output / 'protected-files.json', root)})
    io.write_jsonl(output / 'candidates-v00.jsonl', candidates)
    io.write_json(output / 'selection-v00.json', {'schema_version': 1, 'version': 'test-phase4-v00',
        'baseline_pool': baseline_ref, 'candidates': io.evidence_ref(output / 'candidates-v00.jsonl', root),
        'inventory': io.evidence_ref(output / 'inventory.json', root), 'reviewed_new_tasks': 0,
        'train_candidates_unchanged': True, 'model_requests': 0, 'native_rewards_modified': False,
        'scope': 'Phase4 evaluation eligibility only; no new split and no permission for training.'})
    io.write_json(output / 'state.json', {'schema_version': 1, 'phase': 4,
        'inventory': io.evidence_ref(output / 'inventory.json', root), 'completed': {}, 'ready': {}, 'excluded_reviews': {},
        'latest_freeze': io.evidence_ref(output / 'selection-v00.json', root), 'running_batches': [],
        'completed_batches': [], 'model_requests': 0})
    return {'counts': COUNTS, 'formal_tasks': 37, 'inventory': str(output / 'inventory.json')}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    print(initialize(p.parse_args().output))
