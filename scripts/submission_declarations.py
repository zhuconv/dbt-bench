#!/usr/bin/env python3
"""Bind an explicitly versioned capture declaration without replacing old evidence."""
import argparse
from copy import deepcopy
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ok, evidence_ref, file_hash, read_json, write_json


def normalized_path(value, *, absolute):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Capture paths must be nonempty POSIX paths')
    path = PurePosixPath(value)
    if (str(path) != value or '..' in path.parts or path.is_absolute() != absolute
            or value in ('.', '/', 'manifest.json')):
        raise ValueError('Capture path is not a normalized scoped path: ' + value)
    return path


def bind_versioned_submission(root, config, task_key, spec_path):
    """Validate identity and declared paths before mutating the Harbor config.

    This validates a declaration, not an actual capture, model success, or the
    sufficiency of a business contract. Per-trial inspect_submission still runs.
    """
    root = Path(root).resolve()
    path = root / spec_path
    spec = read_json(path)
    row = next((r for r in load_catalog(root) if r['task_key'] == task_key), None)
    if row is None or spec.get('schema_version') not in (1, 2) or spec.get('task_key') != task_key:
        raise ValueError('Submission capture task identity mismatch')
    current_contract = root / Path(row['validation_ref']).with_name('contract.yaml')
    if (not row.get('adapted_task_hash') or spec.get('task_hash') != row['adapted_task_hash']
            or not evidence_ok(spec.get('contract'), root)
            or spec['contract']['sha256'] != file_hash(current_contract)):
        raise ValueError('Submission capture contract/task changed; review declared paths again')
    for field in ('collection_version', 'rationale'):
        if not isinstance(spec.get(field), str) or not spec[field].strip():
            raise ValueError('Missing reviewed declaration field: ' + field)
    refs = spec.get('path_evidence')
    if not isinstance(refs, list) or not refs or not all(evidence_ok(ref, root) for ref in refs):
        raise ValueError('Submission path review evidence missing or changed')
    if config.get('artifacts'):
        raise ValueError('Do not overwrite existing artifact declarations')
    artifacts = spec.get('artifacts')
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError('Require declared submission artifacts')
    kinds = spec.get('artifact_types', {})
    if spec['schema_version'] == 2 and (
            not isinstance(kinds, dict) or set(kinds) != {a.get('source') for a in artifacts}
            or any(kind not in ('file', 'directory') for kind in kinds.values())):
        raise ValueError('Declare the exact type of every submission artifact')
    sources, destinations = [], []
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) - {'source', 'destination', 'exclude', 'service'}:
            raise ValueError('Unexpected native artifact declaration fields')
        if artifact.get('service') not in (None, 'main'):
            raise ValueError('This capture validator supports the agent main service only')
        source = normalized_path(artifact.get('source'), absolute=True)
        destination = normalized_path(artifact.get('destination'), absolute=False)
        exclusions = artifact.get('exclude', [])
        if not isinstance(exclusions, list) or not all(isinstance(v, str) and v for v in exclusions):
            raise ValueError('Invalid native artifact exclusion patterns')
        if spec['schema_version'] == 2 and kinds[artifact['source']] == 'file' and exclusions:
            raise ValueError('File artifacts cannot have directory exclusions')
        for value, existing in ((source, sources), (destination, destinations)):
            if any(value == old or value in old.parents or old in value.parents for old in existing):
                raise ValueError('Overlapping or duplicate submission paths')
            existing.append(value)
    required = spec.get('required_files')
    if not isinstance(required, list) or not required:
        raise ValueError('Declare required captured files')
    checked = [normalized_path(value, absolute=False) for value in required]
    def covers(artifact, value):
        destination = PurePosixPath(artifact['destination'])
        if spec['schema_version'] == 2 and kinds[artifact['source']] == 'file':
            return value == destination
        return destination in value.parents
    if len(set(checked)) != len(checked) or any(
            sum(covers(artifact, value) for artifact in artifacts) != 1 for value in checked):
        raise ValueError('Required file must belong to exactly one declared artifact')
    # Keep historical contracts, collection policies and trial-local specs intact.
    config['artifacts'] = deepcopy(artifacts)
    return spec, {
        'spec': evidence_ref(path, root), 'contract': spec['contract'],
        'declaration_validator': evidence_ref(Path(__file__), ROOT),
        'scope': 'Reviewed declaration only; pre-verifier native capture is checked after each trial',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--trial', type=Path, help='Inspect an existing native artifact capture; does not run a job')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse to overwrite a declaration review receipt')
    config = {}
    spec, binding = bind_versioned_submission(ROOT, config, args.task, args.spec)
    receipt = {
        'schema_version': 1, 'task_key': args.task, 'declaration_valid': True,
        'collection_version': spec['collection_version'], 'binding': binding,
        'artifacts': config['artifacts'], 'capture_executed': False,
        'model_requests': 0, 'scope': binding['scope'],
    }
    if args.trial is not None:
        from dbt_bench.report import inspect_submission
        receipt['capture'] = inspect_submission(args.trial, spec)
        receipt['scope'] += '; inspected existing artifacts without running an agent or verifier'
    write_json(args.output, receipt)
    print({'declaration_valid': True, 'capture_executed': False,
           'existing_capture_complete': receipt.get('capture', {}).get('complete'),
           'output': str(args.output)})
    return 0 if args.trial is None or receipt['capture']['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
