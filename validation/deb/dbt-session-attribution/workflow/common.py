"""Explicit workflow obligations, independent of the native evaluator."""
from collections import Counter
from independent import compare, encoded

PLAN = {
    'idempotent-control': ('witness', ['R-dbt-workflow', 'R-order', 'R-idempotence']),
    'missing-model': ('negative', ['R-dbt-workflow']),
    'view-output': ('negative', ['R-dbt-workflow']),
    'new-staging': ('negative', ['R-dbt-workflow']),
    'descending-query': ('negative', ['R-order']),
    'append-on-repeat': ('negative', ['R-idempotence', 'R-channel-grain']),
    'wrong-schema': ('negative', ['R-output-location']),
}


def multiset(output):
    return sorted(encoded(r) for r in output['rows'])


def assess(mode, before, phases, expected):
    """Refuse a case unless saved observations establish its declared obligation."""
    assert mode in PLAN and phases
    assert before['model_present'] and before['required_output']['relation_type'] == 'BASE TABLE'
    assert compare(expected, before['required_output'])['equal']
    assert before['query_channels'] == sorted(r['channel'] for r in expected)
    after = phases[-1]
    changed = {k: v for k, v in after['staging_sources'].items()
               if before['staging_sources'].get(k) != v}
    removed = set(before['staging_sources']) - set(after['staging_sources'])
    assert not removed
    if mode != 'new-staging': assert not changed
    correct_values = after['required_output'] is not None and compare(expected, after['required_output'])['equal']
    if mode == 'idempotent-control':
        assert len(phases) == 2 and correct_values
        assert all(p['model_sha256'] == before['model_sha256'] for p in phases)
        assert all(p['required_output']['relation_type'] == 'BASE TABLE' for p in phases)
        assert all(multiset(p['required_output']) == multiset(before['required_output']) for p in phases)
        assert all(p['query_channels'] == before['query_channels'] for p in phases)
        reason = 'Two additional dbt runs preserve all required rows, values, table materialization, source, staging definitions and ascending model-query order.'
    elif mode == 'missing-model':
        assert not after['model_present'] and after['model_sha256'] is None and correct_values
        reason = 'Required models/marts/attribution_report.sql is absent at submission; the previously built correct table remains. Missing deliverable is established before grading.'
    elif mode == 'view-output':
        assert after['model_present'] and correct_values and after['required_output']['relation_type'] == 'VIEW'
        reason = 'Successful dbt run produced a VIEW with correct values; the instruction explicitly requires table materialization.'
    elif mode == 'new-staging':
        assert correct_values and set(changed) == {'models/staging/stg_validation_extra.sql'}
        assert after['extra_relation_type'] == 'VIEW'
        reason = 'An additional staging model was submitted and successfully built, violating the explicit instruction not to create staging models; existing staging files and required output are unchanged.'
    elif mode == 'descending-query':
        wanted = sorted(r['channel'] for r in expected)
        assert len(wanted) > 1 and correct_values and after['query_channels'] == list(reversed(wanted))
        assert after['query_channels'] != wanted
        reason = 'Executing the compiled final model SELECT produces descending channels. The obligation concerns model-query order, not an assumed intrinsic order of a relational table scan.'
    elif mode == 'append-on-repeat':
        assert len(phases) == 2 and compare(expected, phases[0]['required_output'])['equal']
        assert phases[0]['model_sha256'] == after['model_sha256']
        assert after['required_output']['relation_type'] == 'BASE TABLE' and not correct_values
        first = Counter(encoded(r) for r in phases[0]['required_output']['rows'])
        second = Counter(encoded(r) for r in after['required_output']['rows'])
        assert second == Counter({r: 2 * n for r, n in first.items()})
        reason = 'With unchanged source and input, the first successful incremental build is correct; the second successful build doubles every output row, violating idempotence and channel grain.'
    elif mode == 'wrong-schema':
        assert after['required_output'] is None and after['wrong_schema_output']['relation_type'] == 'BASE TABLE'
        assert compare(expected, after['wrong_schema_output'])['equal']
        reason = 'Successful dbt run creates the correct values only in main_case_wrong.attribution_report; the explicitly required main.attribution_report is absent.'
    return {'established': True, 'kind': PLAN[mode][0], 'derivation': reason,
            'required_values_equal': correct_values, 'changed_staging_sources': changed,
            'before_rows': len(before['required_output']['rows']),
            'after_rows': len(after['required_output']['rows']) if after['required_output'] else None}
