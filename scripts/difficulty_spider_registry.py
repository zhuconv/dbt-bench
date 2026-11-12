"""Exact frozen Spider task versions and their qualified table components.

This selects checker code, never submitted SQL. Workflow and original actions
are checked separately; a table component alone cannot certify an attempt.
"""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from dbt_bench import io
ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/spider2'))
ENTRIES = {'app_reporting001': ('original-90ea3077f5c6c70d', 'app001_raw_audit_v1.py'), 'app_reporting002': ('original-0d0a78a771ab46aa', 'app002_raw_audit_v1.py'), 'apple_store001': ('phase3-instruction-050f24b3ea89e40d', 'apple_phase3_audit_v1.py'), 'asset001': ('phase3-instruction-916f5a2b8160b484', 'asset_phase3_audit_v2.py'), 'google_play001': ('original-c36109b19c8bd9c3', 'google_phase3_audit_v1.py'), 'hive001': ('phase3-instruction-1acdf0010675b28c', 'hive_phase3_audit_v2.py'), 'intercom001': ('original-1b4aa19e1437ea38', 'intercom_phase3_audit_v1.py'), 'jira001': ('phase3-instruction-b54cb3dbf67c167a', 'jira_phase3_audit_v1.py'), 'lever001': ('original-8e7628441b2d6775', None), 'marketo001': ('original-f4799de7d61d854e', 'marketo_phase3_audit_v2.py'), 'maturity001': ('original-40cba0620c907d84', 'maturity_phase3_audit_v1.py'), 'recharge002': ('original-c0749c0310631373', 'recharge2_phase3_audit_v1.py'), 'shopify002': ('original-0b04b6b88506c0ff', 'shopify2_phase3_audit_v1.py'), 'tickit001': ('original-fff07de7cc872765', 'tickit_phase3_audit_v1.py'), 'workday001': ('original-e6acfe9bc7f3d895', 'workday1_phase3_audit_v1.py'), 'workday002': ('original-fc3a9532de506010', 'workday2_phase3_audit_v1.py')}
OUTPUT_RULES = {**{name: ('R-required-schema',) for name in ('app_reporting001', 'app_reporting002', 'apple_store001', 'google_play001', 'intercom001', 'jira001', 'marketo001', 'recharge002', 'workday001', 'workday002')}, 'lever001': ('R-required-output-schema',), 'asset001': ('C-grain-date', 'C-quote-source-identity'), 'maturity001': ('C-doctor-source-association', 'C-patient-source-association'), 'hive001': ('R-model-stg_covid__cases', 'R-model-covid_cases'), 'shopify002': ('R-model-shopify__discounts',), 'tickit001': ('R-model-fct_sales',)}

def load(reference, root=ROOT):
    if not io.evidence_ok(reference, root):
        raise ValueError('Changed qualified evidence: ' + reference['path'])
    return io.read_json(root / reference['path'])

def qualified_entry(reference, key, version, root=ROOT):
    name = key.removeprefix('spider2__')
    if key != 'spider2__' + name or name not in ENTRIES or ENTRIES[name][0] != version:
        raise ValueError('Outside the sixteen exact frozen Spider versions')
    record = load(reference, root)
    if record['task_key'] != key or record['task_version'] != version or (not record['eligible_for_sampling']) or (record['audit']['conclusion'] != 'validated'):
        raise ValueError('Wrong or unqualified task version')
    overlay = load(record['audit']['overlay'], root)
    for dep in overlay['code_refs']:
        if not io.evidence_ok(dep, root):
            raise ValueError('Frozen checker changed: ' + dep['path'])
    component = COMPONENT_ROOT / ENTRIES[name][1] if ENTRIES[name][1] else root / 'validation/spider2/lever001/audit_phase2_v1/audit.py'
    if not any(((root / r['path']).resolve() == component.resolve() and r['sha256'] == io.file_hash(component) for r in overlay['code_refs'])):
        raise ValueError('Table component is not in the frozen qualification')
    review = load(record['formal_candidate_evaluation']['review'], root)
    row = next((r for r in review['records'] if r['stage'] == 'agent-submission'))
    result = load(row['audit_result'], root)
    table_result = result.get('stages', {}).get('agent-submission') or result.get('table_stages', {}).get('agent-submission') or result
    raw_ref = table_result['raw_snapshot']
    raw = load(raw_ref, root)
    if raw['task_key'] != key or raw.get('gold_accessed') is not False:
        raise ValueError('Expected qualified original-input projection without gold')
    return {'record': record, 'overlay': overlay, 'checker': component, 'raw_ref': raw_ref, 'raw': raw, 'formal_row': row, 'fixture_only': False}

def required_output_evidence(qualified, root=ROOT):
    """Reviewed rule IDs, not table names guessed from test text."""
    key = qualified['record']['task_key']
    contract_ref = qualified['overlay']['contract']
    contract = load(contract_ref, root)
    rules = {r['id']: r for r in contract['requirements']}
    ids = OUTPUT_RULES[key.removeprefix('spider2__')]
    if any((x not in rules for x in ids)) or qualified['overlay']['checks']['nop']['status'] != 'pass':
        raise ValueError('Required-output absence is not qualified')
    refs = [contract_ref, *qualified['overlay']['checks']['nop']['evidence']]
    for reference in refs:
        if not io.evidence_ok(reference, root):
            raise ValueError('Reviewed absence evidence changed')
    return {'rule_ids': list(ids), 'rules': [rules[x] for x in ids], 'rule_evidence': refs, 'scope': 'Explicit frozen required-output rules and actually executed no-build/absence qualification; source version and original checker remain unchanged.'}
