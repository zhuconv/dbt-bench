"""Explicit qualified DEB entrypoints for difficulty-only submission audits.

The registry selects checker code, never a candidate SQL implementation. The
selected code and every qualification dependency must still match frozen hashes.
"""
from pathlib import Path
from dbt_bench import io

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = {
    'dbt-calculate-running-balance': ('run_audit_v2.py', 'spec'),
    'dbt-coupon-effectiveness': ('run_audit_v2.py', 'spec'),
    'dbt-customer-geographic': ('run_audit_v5.py', 'spec'),
    'dbt-exchange-rate-settlement-date': ('run_audit_v4.py', 'spec'),
    'dbt-fix-cac-payback-waterfall': ('check_v2.py', 'request'),
    'dbt-harbor-warehouse-capacity': ('check_v6.py', 'request'),
    'dbt-inventory-stockout-risk': ('check_v1.py', 'batch03'),
    'dbt-order-reconciliation': ('run_audit_v5.py', 'spec'),
    'dbt-price-elasticity': ('check_v1.py', 'batch03'),
    'dbt-retail-task-04-market-roi': ('check_v5.py', 'request'),
    'dbt-sales-funnel-analysis': ('run_audit_v3.py', 'spec'),
    'dbt-session-attribution': ('run_audit_v3.py', 'spec'),
    'dbt-supplier-payment-optimization': ('run_audit_v4.py', 'spec'),
    'dbt-web-session-analytics': ('check_v1.py', 'batch03'),
    'dbt-weekly-sales-growth': ('check_v4.py', 'request'),
}


def load(reference, root=ROOT):
    if not io.evidence_ok(reference, root):
        raise ValueError('Changed qualified evidence: ' + reference['path'])
    return io.read_json(root / reference['path'])


def qualified_entry(reference, key, version, root=ROOT):
    row = load(reference, root)
    if (row['task_key'] != key or row['task_version'] != version
            or not row['eligible_for_sampling'] or row['audit']['conclusion'] != 'validated'):
        raise ValueError('Wrong task/version or unqualified checker')
    name = key.removeprefix('deb__')
    if key != 'deb__' + name or name not in ENTRIES:
        raise ValueError('Task outside explicit DEB qualification registry')
    overlay = load(row['audit']['overlay'], root)
    for dep in overlay['code_refs']:
        if not io.evidence_ok(dep, root):
            raise ValueError('Frozen checker dependency changed: ' + dep['path'])
    filename, style = ENTRIES[name]
    checker = root / 'validation/deb' / name / 'phase3_v1' / filename
    if not any((root / r['path']).resolve() == checker.resolve()
               and r['sha256'] == io.file_hash(checker) for r in overlay['code_refs']):
        raise ValueError('Entrypoint is outside the qualified checker')
    return {'record': row, 'overlay': overlay, 'checker': checker, 'style': style}
