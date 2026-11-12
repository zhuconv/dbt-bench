"""Declare exact blind source diffs and capture scope for customer phase4 runs."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import json
from dbt_bench import io
R = Path.cwd()
P = Path(_release_expand('${SCRATCH_ROOT}/claude-two-version-pilot'))
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase4-review-v1/customer'))
B = R / 'artifacts/validation/native-mechanical-deb-batch-v1'
FIRST = ['dbt-customer-churn-cohorts', 'dbt-customer-cohort-retention', 'dbt-customer-ltv-fix', 'dbt-order-interval-metrics', 'dbt-rfm-customer-segmentation-2', 'dbt-rfm-customer-tiering']
RAW = {'dbt-customer-churn-cohorts': {'sales': 'SELECT * FROM ANALYTICS.FACT_SALES', 'dim_customer': 'SELECT * FROM ANALYTICS.DIM_CUSTOMER', 'dim_date': 'SELECT * FROM ANALYTICS.DIM_DATE', 'customers': 'SELECT * FROM CUSTOMER.CUSTOMERS', 'stg_sales': 'SELECT * FROM main.stg_analytics__fact_sales', 'stg_dim_customer': 'SELECT * FROM main.stg_analytics__dim_customer', 'stg_dim_date': 'SELECT * FROM main.stg_analytics__dim_date', 'stg_customers': 'SELECT * FROM main.stg_customer__customers'}, 'dbt-customer-cohort-retention': {'orders': 'SELECT order_id,customer_id,ordered_at,grand_total,status FROM main.orders'}, 'dbt-customer-ltv-fix': {'customers': 'SELECT customer_id FROM main.customers', 'orders': 'SELECT customer_id,order_id,grand_total,ordered_at,status,payment_status,test_order_flag,sample_order_flag,internal_order_flag FROM main.orders'}, 'dbt-order-interval-metrics': {'orders': 'SELECT * FROM ORDERS.ORDERS', 'main_orders': 'SELECT * FROM main.orders'}, 'dbt-rfm-customer-segmentation-2': {'orders': 'SELECT * FROM main.stg_orders__orders', 'customers': 'SELECT * FROM main.stg_customer__customers', 'raw_orders': 'SELECT * FROM ORDERS.ORDERS', 'raw_customers': 'SELECT * FROM CUSTOMER.CUSTOMERS'}, 'dbt-rfm-customer-tiering': {'sales': 'SELECT * FROM main.stg_analytics__fact_sales', 'dim_customer': 'SELECT * FROM main.stg_analytics__dim_customer', 'customers': 'SELECT * FROM main.stg_customer__customers', 'tiers': 'SELECT * FROM main.stg_customer__customer_tiers', 'channels': 'SELECT * FROM main.stg_analytics__dim_channel', 'dates': 'SELECT * FROM main.stg_analytics__dim_date', 'raw_sales': 'SELECT * FROM ANALYTICS.FACT_SALES', 'raw_customers': 'SELECT * FROM CUSTOMER.CUSTOMERS'}}

def main():
    D.mkdir(parents=True, exist_ok=True)
    result = io.read_json(P / 'results_test.json')
    inventory = io.read_json(D / 'initial-source-inventory-v1.json')
    master = inventory['project_files']
    rows = []
    reviews = []
    collector = R / 'validation/deb/dbt-customer-cohort-retention/phase4_v1/collect_v1.py'
    for name in FIRST:
        key = 'deb__' + name
        chosen = P / (name + '_v2' if result[name]['outcome'] == 'patched_pass' else name)
        project = '/app/dbt_models_duckdb'
        tree = chosen / 'app' / Path(project).name
        old = master[project]
        files = [p for directory in ('models', 'macros', 'seeds') for p in (tree / directory).rglob('*') if p.is_file() and (not p.is_symlink())]
        files += [p for p in tree.glob('*') if p.is_file() and p.suffix in {'.yml', '.yaml'}]
        changed = [p for p in files if old.get(str(p.relative_to(tree)), {}).get('sha256') != io.file_hash(p)]
        deleted = [p for p in old if p.startswith(('models/', 'macros/', 'seeds/')) and (not (tree / p).exists())]
        assert not deleted, (name, deleted)
        source_files = [{'path': str(p.relative_to(tree)), 'source': io.evidence_ref(p, R)} for p in sorted(changed)]
        base = io.read_json(B / 'tasks' / key / 'mechanical/spec.json')
        outputs = {x['relation'].split('.', 1)[1]: x['relation'] for x in base['outputs']}
        target = 'phase4_target_v1'
        logs = 'phase4_logs_v1'
        capture_files = list(dict.fromkeys([x['path'] for x in source_files] + ['profiles.yml', 'dbt_project.yml', 'packages.yml', 'package-lock.yml', target + '/manifest.json', target + '/run_results.json', logs + '/dbt.log']))
        command = ['dbt', 'run']
        if name == 'dbt-rfm-customer-segmentation-2':
            command += ['--select', 'path:models/marts/rfm']
        command += ['--target-path', target, '--log-path', logs]
        commands = [command]
        if name in {'dbt-customer-churn-cohorts', 'dbt-order-interval-metrics'}:
            commands += [command.copy()]
        spec = {'task_key': key, 'project_root': project, 'database': '/app/database/retail.duckdb', 'raw': RAW[name], 'outputs': outputs, 'files': [f for f in capture_files if not f.startswith((target + '/', logs + '/'))]}
        args = ['--spec-json', json.dumps(spec, separators=(',', ':')), '--target-path', target, '--output', '{output}']
        hooks = [{'phase': 'initial', 'source': io.evidence_ref(collector, R), 'args': args + ['--phase', 'initial']}, *[{'phase': 'after_command', 'command_index': i, 'source': io.evidence_ref(collector, R), 'args': args + ['--phase', 'final']} for i in range(len(commands))], {'phase': 'final', 'source': io.evidence_ref(collector, R), 'args': args + ['--phase', 'final']}]
        rebuild = {'task_key': key, 'project_root': project, 'submission': {'files': source_files, 'reset_paths': [], 'delete_paths': []}, 'commands': commands, 'environment': {'DB_TYPE': 'duckdb', 'DUCKDB_PATH': '/app/database/retail.duckdb', 'DBT_PROFILES_DIR': project}, 'capture_files': capture_files, 'capture_trees': ['models', 'macros'], 'fresh_artifact_paths': [target, logs], 'input_files': ['/app/database/retail.duckdb'], 'protected_paths': ['/app/database/retail.duckdb', '/app/database/retail.duckdb.wal'], 'probe_backend_before_submission': True, 'capture_hooks': hooks, 'timeout_sec': 2700, 'capture_timeout_sec': 300}
        observed = [{'database': spec['database'], 'relation': rel, 'columns': None, 'keys': [], 'ordered': False, 'tolerances': {}} for rel in outputs.values()]
        observation = {'task_key': key, 'source': 'deb', 'reference_kind': 'built_from_inputs', 'database_paths': [spec['database']], 'outputs': observed, 'files': [project + '/' + x for x in capture_files], 'observation_sql_types': True}
        rows.append({'task_key': key, 'label': key + '--phase4-blind-v1', 'rebuild': rebuild, 'observation': observation})
        reviews.append({'task_key': key, 'blind_build': io.evidence_ref(chosen / 'build.txt', R), 'blind_decisions': io.evidence_ref(chosen / 'decisions.md', R), 'instruction': io.evidence_ref(chosen / 'instruction.md', R), 'source_inventory': io.evidence_ref(D / 'initial-source-inventory-v1.json', R), 'changed_files': source_files, 'deleted_files': deleted, 'command_adaptation': 'Exact dbt action/selection from build.txt, explicit isolated target/log paths only. Where instruction explicitly requires idempotence, repeat that same command once within each independent clean trial. Profiles/env reproduced from build.txt. No SQL, hidden answers, database, compiled target, or package installation added.', 'outputs_basis': io.evidence_ref(B / 'tasks' / key / 'mechanical/spec.json', R), 'raw_scope': RAW[name]})
    for path, value in [(D / 'batch01-requests-v1.json', {'schema_version': 1, 'model_requests': 0, 'n_attempts': 2, 'tasks': rows}), (D / 'batch01-input-review-v1.json', {'schema_version': 1, 'model_requests': 0, 'tasks': reviews})]:
        assert not path.exists()
        io.write_json(path, value)
    print({'tasks': len(rows), 'requests': str(D / 'batch01-requests-v1.json'), 'source_counts': {x['task_key']: len(x['rebuild']['submission']['files']) for x in rows}})
if __name__ == '__main__':
    main()
