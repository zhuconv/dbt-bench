"""Declare remaining blind builds, preserving each upstream and final dbt step."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
import json
from dbt_bench import io
R = Path.cwd()
P = Path(_release_expand('${SCRATCH_ROOT}/claude-two-version-pilot'))
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase4-review-v1/customer'))
B = R / 'artifacts/validation/native-mechanical-deb-batch-v1'
BATCH2 = ['dbt-customer-churn-early-warning', 'dbt-customer-cltv-forecasting', 'dbt-customer-order-analytics', 'dbt-fix-repeat-purchase-cohort-revenue', 'dbt-rfm-customer-segmentation', 'dbt-fix-email-attribution']
BATCH3 = ['dbt-customer-lifetime-value', 'dbt-fix-multi-touch-attribution', 'dbt-fix-paid-search-attribution', 'dbt-loyalty-points-analysis', 'dbt-consolidate']
RAW = {'dbt-customer-churn-early-warning': {'enriched': 'SELECT * FROM main.int_sales__orders_enriched'}, 'dbt-customer-cltv-forecasting': {'enriched': 'SELECT order_id,customer_id,ordered_at,grand_total,is_cancelled FROM main.int_sales__orders_enriched'}, 'dbt-customer-order-analytics': {'customers': 'SELECT * FROM CUSTOMER.CUSTOMERS', 'orders': 'SELECT * FROM ORDERS.ORDERS', 'main_customers': 'SELECT * FROM main.customers', 'main_orders': 'SELECT * FROM main.orders'}, 'dbt-fix-repeat-purchase-cohort-revenue': {'enriched': 'SELECT order_id,customer_id,ordered_at,grand_total FROM main.int_sales__orders_enriched'}, 'dbt-rfm-customer-segmentation': {'customers': 'SELECT customer_id,customer_number,first_name,last_name,status FROM main.customers', 'orders': 'SELECT customer_id,order_id,grand_total,ordered_at,status FROM main.orders'}, 'dbt-fix-email-attribution': {'sessions': 'SELECT * FROM main.stg_ga__sessions', 'events': 'SELECT * FROM main.stg_ga__events', 'joined': 'SELECT * FROM main.int_sessions_events_joined'}, 'dbt-customer-lifetime-value': {'orders': 'SELECT order_id,customer_id,ordered_at,grand_total,status,test_order_flag FROM main.stg_orders__orders', 'customers': 'SELECT customer_id,first_name,last_name,status FROM main.stg_customer__customers'}, 'dbt-fix-multi-touch-attribution': {'sessions': 'SELECT * FROM main.stg_ga__sessions', 'events': 'SELECT * FROM main.stg_ga__events', 'joined': 'SELECT * FROM main.int_sessions_events_joined'}, 'dbt-fix-paid-search-attribution': {'sessions': 'SELECT * FROM main.stg_ga__sessions', 'events': 'SELECT * FROM main.stg_ga__events', 'joined': 'SELECT * FROM main.int_sessions_events_joined'}, 'dbt-loyalty-points-analysis': {'transactions': 'SELECT * FROM main.LOYALTY_POINTS_TRANSACTIONS', 'programs': 'SELECT * FROM main.LOYALTY_PROGRAMS', 'customers': 'SELECT * FROM main.CUSTOMERS'}}
UPSTREAM = {'dbt-customer-churn-early-warning': ['+int_sales__orders_enriched'], 'dbt-fix-repeat-purchase-cohort-revenue': ['int_sales__orders_enriched'], 'dbt-fix-email-attribution': ['stg_ga__sessions', 'stg_ga__events', 'int_sessions_events_joined']}
EXISTING = {'dbt-customer-lifetime-value', 'dbt-fix-paid-search-attribution'}

def main():
    result = io.read_json(P / 'results_test.json')
    master = io.read_json(D / 'initial-source-inventory-v1.json')['project_files']
    collector = R / 'validation/deb/dbt-customer-cohort-retention/phase4_v1/collect_v1.py'
    for batchno, names in [(2, BATCH2), (3, BATCH3)]:
        rows = []
        reviews = []
        for name in names:
            if name == 'dbt-consolidate':
                continue
            key = 'deb__' + name
            chosen = P / (name + '_v2' if result[name]['outcome'] == 'patched_pass' else name)
            project = '/app/dbt_models_duckdb' if name in EXISTING else '/app/dbt_project'
            tree = chosen / 'app' / Path(project).name
            old = master[project]
            files = [p for directory in ('models', 'macros', 'seeds') for p in (tree / directory).rglob('*') if p.is_file() and (not p.is_symlink())]
            files += [p for p in tree.glob('*') if p.is_file() and p.suffix in {'.yml', '.yaml'}]
            changed = [p for p in files if old.get(str(p.relative_to(tree)), {}).get('sha256') != io.file_hash(p)]
            deleted = [p for p in old if p.startswith(('models/', 'macros/', 'seeds/')) and (not (tree / p).exists())]
            assert not deleted, (name, deleted)
            sources = [{'path': str(p.relative_to(tree)), 'source': io.evidence_ref(p, R)} for p in sorted(changed)]
            base = io.read_json(B / 'tasks' / key / 'mechanical/spec.json')
            outputs = {x['relation'].split('.', 1)[1]: x['relation'] for x in base['outputs']}
            target = 'phase4_target_v1'
            logs = 'phase4_logs_v1'
            up_target = 'phase4_upstream_target_v1'
            up_logs = 'phase4_upstream_logs_v1'
            capture_files = list(dict.fromkeys([x['path'] for x in sources] + ['profiles.yml', 'dbt_project.yml', 'packages.yml', 'package-lock.yml', target + '/manifest.json', target + '/run_results.json', logs + '/dbt.log']))
            commands = []
            if name in UPSTREAM:
                commands.append(['dbt', 'run', '--project-dir', '/app/dbt_models_duckdb', '--profiles-dir', '/app/dbt_models_duckdb', '--select', *UPSTREAM[name], '--target-path', project + '/' + up_target, '--log-path', project + '/' + up_logs])
                capture_files += [up_target + '/manifest.json', up_target + '/run_results.json', up_logs + '/dbt.log']
            command = ['dbt', 'run']
            if name == 'dbt-customer-lifetime-value':
                command += ['--select', '+clv_segments', '+clv_segment_summary', '+clv_cohort_analysis', '+clv_monthly_trends']
            if name == 'dbt-fix-paid-search-attribution':
                command += ['--select', 'rpt_paid_search_attribution_fixed']
            command += ['--target-path', target, '--log-path', logs]
            commands.append(command)
            instruction = (chosen / 'instruction.md').read_text().lower()
            extra_repeat = 'idempoten' in instruction or 'multiple runs' in instruction
            if extra_repeat:
                commands.append(command.copy())
            spec = {'task_key': key, 'project_root': project, 'database': '/app/database/retail.duckdb', 'raw': RAW[name], 'outputs': outputs, 'files': [f for f in capture_files if not f.startswith((target + '/', logs + '/', up_target + '/', up_logs + '/'))]}
            args = ['--spec-json', json.dumps(spec, separators=(',', ':')), '--target-path', target, '--output', '{output}']
            hooks = [{'phase': 'initial', 'source': io.evidence_ref(collector, R), 'args': args + ['--phase', 'initial']}]
            for i, cmd in enumerate(commands):
                hooks.append({'phase': 'after_command', 'command_index': i, 'source': io.evidence_ref(collector, R), 'args': args + ['--phase', 'initial' if '--project-dir' in cmd else 'final']})
            hooks.append({'phase': 'final', 'source': io.evidence_ref(collector, R), 'args': args + ['--phase', 'final']})
            fresh = [target, logs] + ([up_target, up_logs] if name in UPSTREAM else [])
            rebuild = {'task_key': key, 'project_root': project, 'submission': {'files': sources, 'reset_paths': [], 'delete_paths': []}, 'commands': commands, 'environment': {'DB_TYPE': 'duckdb', 'DUCKDB_PATH': spec['database'], 'DBT_PROFILES_DIR': project}, 'capture_files': capture_files, 'capture_trees': ['models', 'macros'], 'fresh_artifact_paths': fresh, 'input_files': [spec['database']], 'protected_paths': [spec['database'], spec['database'] + '.wal'], 'probe_backend_before_submission': True, 'capture_hooks': hooks, 'timeout_sec': 2700, 'capture_timeout_sec': 300}
            observation = {'task_key': key, 'source': 'deb', 'reference_kind': 'built_from_inputs', 'database_paths': [spec['database']], 'outputs': [{'database': spec['database'], 'relation': rel, 'columns': None, 'keys': [], 'ordered': False, 'tolerances': {}} for rel in outputs.values()], 'files': [project + '/' + x for x in capture_files], 'observation_sql_types': True}
            rows.append({'task_key': key, 'label': key + '--phase4-blind-v1', 'rebuild': rebuild, 'observation': observation})
            reviews.append({'task_key': key, 'blind_build': io.evidence_ref(chosen / 'build.txt', R), 'blind_decisions': io.evidence_ref(chosen / 'decisions.md', R), 'instruction': io.evidence_ref(chosen / 'instruction.md', R), 'source_inventory': io.evidence_ref(D / 'initial-source-inventory-v1.json', R), 'changed_files': sources, 'deleted_files': deleted, 'command_adaptation': 'Exact authored action/selection, upstream steps preserved using explicit --project-dir/--profiles-dir and output dirs relocated under controlled project root. SQL and raw input untouched. Repeat final command only for explicit idempotence requirement. No full-app/database/compiled-target copy.', 'extra_repeat': extra_repeat, 'outputs_basis': io.evidence_ref(B / 'tasks' / key / 'mechanical/spec.json', R), 'raw_scope': RAW[name]})
        for p, v in [(D / f'batch{batchno:02d}-requests-v1.json', {'schema_version': 1, 'model_requests': 0, 'n_attempts': 2, 'tasks': rows}), (D / f'batch{batchno:02d}-input-review-v1.json', {'schema_version': 1, 'model_requests': 0, 'tasks': reviews})]:
            assert not p.exists()
            io.write_json(p, v)
        print({'batch': batchno, 'tasks': len(rows), 'requests': str(D / f'batch{batchno:02d}-requests-v1.json')})
if __name__ == '__main__':
    main()
