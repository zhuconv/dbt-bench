"""One user-approved reporting-only identity addition; historical code stays exact.

Approved 2026-11-09. This is not a wildcard report-module exclusion. The old
identity and checker remain in every review; actual identity and the exception
are recorded alongside them. All other fields/files must compare exactly.
"""
from contextlib import contextmanager
from copy import deepcopy
from importlib.abc import MetaPathFinder
from pathlib import Path
import sys
from types import FunctionType

from dbt_bench import io
from dbt_bench.validation import identity
import phase4_review as original_review
import phase4_publish as original_publish
from review_tools import ROOT, cached_hashes

REPORT_PATH = 'src/dbt_bench/report_v6.py'
REPORT_SHA256 = '20cd8f08f6fdfedfda98ae21200858c4928b636b33cd4ea5159a284bfe3392aa'
REPORT_MODULE = 'dbt_bench.report_v6'


def compatible_identity(current, historical, root=ROOT):
    """Accept precisely one additional named/hash-bound file, without mutation."""
    ref = {'path': REPORT_PATH, 'sha256': REPORT_SHA256}
    if not io.evidence_ok(ref, root):
        raise ValueError('Approved report file is missing or its exact hash changed')
    name = Path(REPORT_PATH).name
    if name in historical['implementation']:
        raise ValueError('Compatibility applies only to the approved historical addition')
    if current['implementation'].get(name) != REPORT_SHA256:
        raise ValueError('Current identity lacks the exact approved report addition')
    reduced = deepcopy(current)
    del reduced['implementation'][name]
    if reduced != historical:
        raise ValueError('Unapproved original environment/task/checker identity drift')
    return {'version': 'phase4-report-identity-v2', 'approved_date': '2026-11-09',
            'allowed_addition': ref, 'historical_identity_sha256': io.digest(historical),
            'current_identity_sha256': io.digest(current),
            'current_identity': current, 'all_other_identity_fields_exact': True,
            'implementation': io.evidence_ref(Path(__file__), root)}


@contextmanager
def without_reporting_imports():
    """Fail closed if qualification directly or transitively imports reporting."""
    if REPORT_MODULE in sys.modules:
        raise ValueError('Reporting module already loaded in qualification process')

    class DenyReporting(MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == REPORT_MODULE or fullname.startswith(REPORT_MODULE + '.'):
                raise ValueError('Reporting import attempted during qualification')
            return None

    guard = DenyReporting()
    sys.meta_path.insert(0, guard)
    try:
        yield
        if REPORT_MODULE in sys.modules:
            raise ValueError('Reporting module entered qualification process')
    finally:
        sys.meta_path.remove(guard)


def precheck(path, root=ROOT):
    """Original checks plus a narrowly bound compatibility companion."""
    root = Path(root)
    with without_reporting_imports(), cached_hashes():
        overlay = io.read_json(path)
        supplied = overlay['phase4_identity']
        if not io.evidence_ok(supplied['batch_manifest'], root):
            raise ValueError('Batch manifest changed')
        manifest_path = root / supplied['batch_manifest']['path']
        expected = original_review.binding(manifest_path.parent, supplied['label'], root)
        row = io.unique_index(io.read_jsonl(root / 'catalog/tasks.jsonl'), 'task_key')[expected['task_key']]
        base = io.read_json(root / expected['base_record']['path'])
        current = identity(root, row, (root / row['validation_ref']).parent / 'contract.yaml', base['identity']['backend'])
        companion = compatible_identity(current, expected['base_identity'], root)
        contract = io.read_json(root / overlay['contract']['path'])
        result = original_review.check_overlay(overlay, contract, expected, root)
    return {**result, 'overlay': io.evidence_ref(path, root), 'contract': overlay['contract'],
            'phase4_identity': expected, 'identity_compatibility': companion,
            'reporting_module_imported': False}


def stage(*args, **kwargs):
    """Reuse all original qualification criteria without modifying its globals."""
    original = original_publish.stage
    scope = {**original.__globals__, 'precheck': precheck}
    bound = FunctionType(original.__code__, scope, original.__name__, original.__defaults__, original.__closure__)
    bound.__kwdefaults__ = original.__kwdefaults__
    with without_reporting_imports():
        return bound(*args, **kwargs)
