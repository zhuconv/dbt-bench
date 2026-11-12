"""Compare recorded initial inputs without confusing gzip headers with data."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path


def stable_inputs(document, folder):
    value = deepcopy(document)
    for key in ('captured_at_utc', 'sql_clock'):
        value.pop(key, None)
    # Airbnb's exporter uses gzip's current timestamp. Validate each original
    # archive, then compare its full decoded payload, not its volatile header.
    for name, table in value.get('relations', {}).items():
        if 'compact_file' not in table:
            continue
        path = Path(folder) / table['compact_file']
        if not path.resolve().is_relative_to(Path(folder).resolve()):
            raise ValueError('Compact input escaped its capture directory')
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != table.pop('compact_sha256'):
            raise ValueError('Compact raw input archive changed: ' + name)
        table['decoded_full_payload'] = json.loads(gzip.decompress(payload))
    return value


def validate_initial(actual_path, baseline_path):
    actual_path, baseline_path = Path(actual_path), Path(baseline_path)
    actual = json.loads(actual_path.read_text())
    baseline = json.loads(baseline_path.read_text())
    if not actual.get('unchanged'):
        raise ValueError('Initial capture mutated its database')
    if stable_inputs(actual, actual_path.parent) != stable_inputs(baseline, baseline_path.parent):
        raise ValueError('Actual initial relations differ from qualified inputs')
    return True
