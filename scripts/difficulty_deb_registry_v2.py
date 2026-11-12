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



PHASE4 = {
    'dbt-customer-churn-cohorts':('scripts/phase4_deb_recompute_v2.py','phase4_pipeline'),
    'dbt-customer-churn-early-warning':('scripts/phase4_deb_recompute_v2.py','phase4_pipeline'),
    'dbt-rfm-customer-segmentation-2':('scripts/phase4_deb_recompute_v2.py','phase4_pipeline'),
    'dbt-customer-cltv-forecasting':('validation/deb/dbt-customer-cltv-forecasting/phase4_v1/audit_v1.py','phase4_customer'),
    'dbt-customer-ltv-fix':('validation/deb/dbt-customer-ltv-fix/phase4_v1/audit_v1.py','phase4_customer'),
    'dbt-customer-order-analytics':('validation/deb/dbt-customer-cohort-retention/phase4_v1/extra_audits_v4.py','phase4_customer'),
    'dbt-fix-email-attribution':('validation/deb/dbt-fix-email-attribution/audit_phase2_v1/audit_v1.py','phase4_customer'),
    'dbt-fix-multi-touch-attribution':('validation/deb/dbt-customer-cohort-retention/phase4_v1/extra_audits_v3.py','phase4_customer'),
    'dbt-customer-lifetime-value':('validation/deb/dbt-customer-lifetime-value/phase4_v2/audit_v2.py','phase4_customer'),
    'dbt-loyalty-points-analysis':('validation/deb/dbt-loyalty-points-analysis/phase4_v2/audit_v1.py','phase4_customer'),
    'dbt-rfm-customer-segmentation':('validation/deb/dbt-rfm-customer-segmentation/phase4_v2/audit_v1.py','phase4_customer'),
    'dbt-customer-risk-scoring':('validation/deb/dbt-customer-risk-scoring/phase4_v1/run_audit_v2.py','phase4_operations'),
    'dbt-daily-order-summary':('validation/deb/dbt-daily-order-summary/phase4_v1/run_audit_v2.py','phase4_operations'),
    'dbt-fraud-detection-model':('validation/deb/dbt-fraud-detection-model/phase4_v1/run_audit_v1.py','phase4_operations'),
    'dbt-inventory-analysis':('validation/deb/dbt-inventory-analysis/phase4_v1/check_v3.py','phase4_operations'),
    'dbt-product-category-analytics':('validation/deb/dbt-product-category-analytics/phase4_v1/check_v1.py','phase4_operations'),
    'dbt-retail-task-01-channel-revenue':('validation/deb/dbt-retail-task-01-channel-revenue/phase4_v1/audit_v3.py','phase4_operations'),
    'dbt-supplier-scorecard':('validation/deb/dbt-supplier-scorecard/phase4_v1/audit_v1.py','phase4_operations'),
    'dbt-three-way-matching':('validation/deb/dbt-three-way-matching/phase4_v1/audit_v2.py','phase4_operations'),
    'dbt-fulfillment-sla':('validation/deb/dbt-fulfillment-sla/phase4_v1/audit_v1.py','phase4_operations'),
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
    phase=4 if 'phase4_identity' in row else 3
    registry=PHASE4 if phase==4 else ENTRIES
    if key != 'deb__' + name or name not in registry:
        raise ValueError('Task outside explicit DEB qualification registry')
    overlay = load(row['audit']['overlay'], root)
    for dep in overlay['code_refs']:
        if not io.evidence_ok(dep, root):
            raise ValueError('Frozen checker dependency changed: ' + dep['path'])
    filename, style = registry[name]
    checker = root / filename if phase==4 else root / 'validation/deb' / name / 'phase3_v1' / filename
    if not any((root / r['path']).resolve() == checker.resolve()
               and r['sha256'] == io.file_hash(checker) for r in overlay['code_refs']):
        raise ValueError('Entrypoint is outside the qualified checker')
    return {'record': row, 'overlay': overlay, 'checker': checker, 'style': style, 'phase':phase}
