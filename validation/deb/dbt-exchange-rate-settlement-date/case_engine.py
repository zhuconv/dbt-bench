"""Build scoped FX cases and establish independent proof before native grading."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

import duckdb
from independent import INPUTS, KEYS, encoded, canonical_inputs, derive, compare

PROJECT = Path(os.environ.get('DBT_PROJECT_DIR_DUCKDB', '/app/dbt_models_duckdb'))
DB = Path(os.environ.get('DUCKDB_PATH', '/app/database/retail.duckdb'))
MODEL = PROJECT / 'models/marts/finance/fact_revenue.sql'
LOGS = Path('/logs/agent')
MODES = {
    'reference': ('witness', ['R-settlement', 'R-fx', 'R-usd-values']),
    'reordered': ('equivalent', ['R-order']),
    'duplicate-row': ('negative', ['R-unique-grain']),
    'missing-row': ('negative', ['R-complete-grain']),
    'missing-count-column': ('negative', ['R-preserve-columns']),
    'null-usd-for-null-currency': ('negative', ['R-usd-values', 'R-null-keys']),
    'order-date-rate': ('negative', ['R-fx']),
    'view-materialization': ('negative', ['R-materialization']),
}


def ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def inputs():
    tables = {}
    with duckdb.connect(str(DB), read_only=True) as conn:
        for name in INPUTS:
            query = conn.execute('select * from main.' + name)
            columns = [c[0].lower() for c in query.description]
            tables[name] = {'rows': [dict(zip(columns, row)) for row in query.fetchall()]}
    return canonical_inputs(json.loads(encoded(tables)))


def snapshot(label):
    out = LOGS / ('fx-' + label)
    out.mkdir(exist_ok=False)
    with duckdb.connect(str(DB), read_only=True) as conn:
        query = conn.execute('select * from main.fact_revenue')
        columns = [c[0].lower() for c in query.description]
        rows = [dict(zip(columns, row)) for row in query.fetchall()]
        types = dict(conn.execute("select column_name,data_type from information_schema.columns where table_schema='main' and table_name='fact_revenue'").fetchall())
        kind = conn.execute("select table_type from information_schema.tables where table_schema='main' and table_name='fact_revenue'").fetchone()[0]
    for relative in ['models/marts/finance/fact_revenue.sql', 'dbt_project.yml', 'profiles.yml', 'target/run_results.json', 'target/manifest.json']:
        source = PROJECT / relative
        if source.is_file():
            dest = out / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
    data = {'columns': columns, 'rows': rows, 'types': types, 'table_type': kind,
            'dbt_updated_at_values': sorted({str(r.get('dbt_updated_at')) for r in rows}),
            'files': [ref(p) for p in sorted(out.rglob('*')) if p.is_file()],
            'python': sys.executable, 'duckdb_version': duckdb.__version__}
    path = out / 'snapshot.json'
    path.write_text(encoded(data) + '\n')
    return json.loads(path.read_text()), path


def main(mode):
    kind, requirements = MODES[mode]
    config = json.loads(Path(__file__).with_name('case.json').read_text())
    original = inputs()
    source = LOGS / 'fx-inputs.json'
    source.write_text(encoded(original) + '\n')
    assert ref(source)['sha256'] == config['pristine_inputs_sha256'], 'Input identity mismatch'
    expected, grain = derive(original)
    before, before_path = snapshot('before')
    baseline = compare(expected, before, config['preserved_columns'])
    assert baseline['equal'], baseline
    assert before['table_type'] == 'BASE TABLE'
    sql = MODEL.read_text()
    original_sql_hash = hashlib.sha256(sql.encode()).hexdigest()
    assert original_sql_hash == config['native_reference_model_sha256']
    if mode != 'reference':
        if mode == 'order-date-rate':
            old, new = 'and s.settlement_date = fx.rate_date', 'and s.order_date = fx.rate_date'
        elif mode == 'view-materialization':
            old, new = "materialized='table'", "materialized='view'"
        else:
            old = 'select * from final'
            new = {
                'reordered': 'select * from final order by order_date desc, settlement_date desc, currency_code desc nulls first, source_system desc nulls first',
                'duplicate-row': 'select * from final union all select * from (select * from final order by order_date, settlement_date, currency_code nulls first, source_system nulls first limit 1)',
                'missing-row': 'select * from final order by order_date, settlement_date, currency_code nulls first, source_system nulls first offset 1',
                'missing-count-column': 'select * exclude(total_orders) from final',
                'null-usd-for-null-currency': 'select * replace(case when currency_code is null then null else net_revenue_usd end as net_revenue_usd, case when currency_code is null then null else total_revenue_usd end as total_revenue_usd) from final',
            }[mode]
        assert sql.count(old) == 1, (mode, old)
        MODEL.write_text(sql.replace(old, new))
        run = subprocess.run(['dbt', 'run', '--select', 'fact_revenue', '--profiles-dir', str(PROJECT)],
                             cwd=PROJECT, capture_output=True, text=True)
        (LOGS / 'case-build.log').write_text(run.stdout + run.stderr)
        assert run.returncode == 0, 'Case did not build; not a semantic negative'
    after, after_path = snapshot('after')
    result = compare(expected, after, config['preserved_columns'])
    assert encoded(inputs()) == encoded(original), 'Reference inputs were changed'
    facts = {'baseline_comparison': baseline, 'comparison': result, 'grain_review': grain,
             'before_rows': len(before['rows']), 'after_rows': len(after['rows']),
             'after_table_type': after['table_type'], 'source_inputs_unchanged': True}
    if kind != 'negative':
        assert result['equal']
        derivation = 'Independent Python payment maxima, date fallback, exact key grouping and Decimal revenue sums reproduce all declared core FX/count values. This is scoped to the reference four-key interpretation; the prompt original-grain ambiguity remains unresolved.'
    elif mode == 'duplicate-row':
        assert len(result['duplicates']) == 1 and result['duplicates'][0]['count'] == 2
        assert not result['value_mismatches'] and not result['missing_keys'] and not result['extra_keys']
        derivation = 'The same exact revenue grain key occurs twice after a successful dbt build. Its values remain correct individually, but the duplicate violates unique grain under either original or settlement-extended grouping and double counts that revenue.'
    elif mode == 'missing-row':
        assert len(result['missing_keys']) == 1 and not result['value_mismatches']
        derivation = 'One nonempty source-derived settlement revenue group is absent from the successfully built model.'
    elif mode == 'missing-count-column':
        assert result['missing_columns'] == ['total_orders'] and not result['value_mismatches']
        derivation = 'The initial model exposes total_orders, and the instruction explicitly preserves every output column. The rebuilt submission removes only that required column.'
    elif mode == 'null-usd-for-null-currency':
        assert result['value_mismatches'] and all(m['key'][2] is None and m['column'] in {'net_revenue_usd', 'total_revenue_usd'} and m['actual'] == 'None' and m['expected'] != 'None' for m in result['value_mismatches'])
        derivation = 'For source groups with NULL currency, non-NULL local revenue and fallback fx_rate=1 imply numeric USD revenue. The rebuilt model instead returns NULL USD amounts; independent Decimal products establish the required numeric values before native grading.'
    elif mode == 'order-date-rate':
        assert result['value_mismatches'] and any(m['column'] == 'fx_rate' for m in result['value_mismatches'])
        derivation = 'The successful model uses the order date for FX lookup; independently selected payment settlement dates have different source dimension rates and produce different required USD values.'
    elif mode == 'view-materialization':
        assert result['equal'] and after['table_type'] == 'VIEW'
        derivation = 'The original model explicitly materializes a table. The successful altered model materializes a view despite the explicit instruction not to change materialization; core business values remain identical.'
    else:
        raise AssertionError(mode)
    proof = {'schema_version': 1, 'case': mode, 'requirement_ids': requirements,
             'validity': {'witness': 'built_from_inputs', 'equivalent': 'legal_equivalent', 'negative': 'business_error'}[kind],
             'tested_checker_invoked_to_define_validity': False, 'derivation': derivation,
             'scope': 'Original fixed input; core FX and explicitly named requirement only. No full task certification or resolution of original-vs-reference grain conflict.',
             'facts': facts, 'input_artifacts': [ref(source)],
             'submission_artifacts': [ref(before_path), ref(after_path), ref(MODEL)]}
    Path(os.environ['DBT_BENCH_PROOF']).write_text(encoded(proof) + '\n')
    print(encoded({'case': mode, 'validity': proof['validity'], 'facts': facts}))


if __name__ == '__main__':
    main(sys.argv[1])
