"""Explicit per-task submission exports through Harbor's native artifact API."""
from pathlib import Path

from .catalog import load_catalog
from .io import contained, digest, evidence_ok, evidence_ref, read_json, tree_manifest


def bind_submission(root, config, task_key):
    root = Path(root)
    path = root / 'configs/submissions' / (task_key + '.json')
    spec = read_json(path)
    row = next(r for r in load_catalog(root) if r['task_key'] == task_key)
    if spec.get('schema_version') != 1 or spec.get('task_key') != task_key:
        raise ValueError('Submission capture task identity mismatch')
    if spec['task_hash'] != row['adapted_task_hash'] or not evidence_ok(spec['contract'], root):
        raise ValueError('Submission capture contract/task changed; review declared paths again')
    if not spec['artifacts'] or len({a['source'] for a in spec['artifacts']}) != len(spec['artifacts']):
        raise ValueError('Require distinct, declared submission sources')
    if config.get('artifacts'):
        raise ValueError('Do not overwrite existing artifact declarations')
    config['artifacts'] = spec['artifacts']
    return spec, {'spec': evidence_ref(path, root), 'contract': spec['contract'],
                  'scope': 'Pre-verifier native Harbor artifact collection; capture is checked after each trial'}


def inspect_submission(trial_dir, spec):
    """Missing/failed artifacts cannot become complete merely because reward is one."""
    directory = Path(trial_dir) / 'artifacts'
    result = {'complete': False, 'errors': [], 'files': [], 'snapshot_hash': None,
              'scope': 'Collected bytes only; semantic correctness and checker binding are separate'}
    try:
        entries = read_json(directory / 'manifest.json')
        if not isinstance(entries, list):
            raise ValueError('Invalid native artifact manifest')
        files = []
        for declared in spec['artifacts']:
            matching = [e for e in entries if e.get('source') == declared['source']
                        and e.get('service') in (None, 'main')]
            if len(matching) != 1:
                raise ValueError('Missing or duplicated artifact source: ' + declared['source'])
            entry = matching[0]
            # Harbor 0.25.0 records destinations relative to the trial root,
            # while ArtifactConfig destinations are relative to artifacts/.
            if (entry.get('status') != 'ok' or entry.get('destination') != 'artifacts/' + declared['destination']
                    or entry.get('exclude') != declared.get('exclude', [])):
                raise ValueError('Artifact collection failed or differs from declaration: ' + declared['source'])
            target = contained(directory, declared['destination'])
            if entry.get('type') != 'directory' or not target.is_dir():
                raise ValueError('Expected a complete submission directory')
            inventory = tree_manifest(target)
            if not inventory:
                raise ValueError('Empty submission directory')
            files.extend({**f, 'path': declared['destination'] + '/' + f['path']} for f in inventory)
        for relative in spec['required_files']:
            if not contained(directory, relative).is_file():
                raise ValueError('Required captured file missing: ' + relative)
        result.update(complete=True, files=sorted(files, key=lambda f: f['path']),
                      snapshot_hash=digest(sorted(files, key=lambda f: f['path'])),
                      native_manifest=evidence_ref(directory / 'manifest.json', Path(trial_dir)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result['errors'].append(str(exc))
    return result
