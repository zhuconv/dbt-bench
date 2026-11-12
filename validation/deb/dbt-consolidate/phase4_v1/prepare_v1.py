from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from dbt_bench import io
R = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase4-review-v1/customer'))
P = Path(_release_expand('${SCRATCH_ROOT}/claude-two-version-pilot/dbt-consolidate'))
K = 'deb__dbt-consolidate'
project = '/app/dbt_consolidate'
tree = P / 'app/dbt_consolidate'
proof = io.read_json(D / 'consolidate-dependency-inventory-v2.json')
assert proof['author_package_exact'] and all(proof['author_public_csv_exact'].values())
files = [p for directory in ('models', 'macros', 'seeds', 'dbt_packages') for p in (tree / directory).rglob('*') if p.is_file() and (not p.is_symlink())]
files += [p for p in tree.glob('*') if p.is_file() and p.suffix in {'.yml', '.yaml'}]
assert all((not p.is_symlink() for p in files))
source = [{'path': str(p.relative_to(tree)), 'source': io.evidence_ref(p, R)} for p in sorted(files)]
target = 'phase4_target_v1'
logs = 'phase4_logs_v1'
capture_files = [x['path'] for x in source] + [target + '/manifest.json', target + '/run_results.json', logs + '/dbt.log', 'package-lock.yml']
capture_files = list(dict.fromkeys(capture_files))
commands = [['dbt', action, '--target-path', target, '--log-path', logs] for action in ['seed', 'run', 'test']]
collector = R / 'validation/deb/dbt-consolidate/phase4_v1/collect_v1.py'
args = ['--target-path', target, '--output', '{output}']
hooks = [{'phase': 'initial', 'source': io.evidence_ref(collector, R), 'args': args + ['--phase', 'initial']}, *[{'phase': 'after_command', 'command_index': i, 'source': io.evidence_ref(collector, R), 'args': args + ['--phase', 'final']} for i in range(3)], {'phase': 'final', 'source': io.evidence_ref(collector, R), 'args': args + ['--phase', 'final']}]
rebuild = {'task_key': K, 'project_root': project, 'submission': {'files': source, 'reset_paths': [], 'delete_paths': []}, 'commands': commands, 'environment': {'DB_TYPE': 'duckdb', 'DBT_PROFILES_DIR': project}, 'capture_files': capture_files, 'capture_trees': ['models', 'macros', 'dbt_packages', 'seeds'], 'fresh_artifact_paths': [target, logs], 'input_files': ['/app/data/' + x + '.csv' for x in ['googleads', 'metaads', 'tiktokads']], 'protected_paths': ['/app/data'], 'probe_backend_before_submission': True, 'capture_hooks': hooks, 'timeout_sec': 2700, 'capture_timeout_sec': 300}
base = io.read_json(R / 'artifacts/validation/native-mechanical-deb-batch-v1/tasks' / K / 'mechanical/spec.json')
outputs = [{**x, 'columns': None, 'keys': [], 'tolerances': {}, 'ordered': False} for x in base['outputs']]
observation = {'task_key': K, 'source': 'deb', 'reference_kind': 'built_from_inputs', 'database_paths': ['/app/consolidate.duckdb'], 'outputs': outputs, 'files': [project + '/' + x for x in capture_files], 'observation_sql_types': True}
request = {'schema_version': 1, 'model_requests': 0, 'n_attempts': 2, 'tasks': [{'task_key': K, 'label': K + '--phase4-blind-v1', 'rebuild': rebuild, 'observation': observation}]}
review = {'task_key': K, 'model_requests': 0, 'build': io.evidence_ref(P / 'build.txt', R), 'decisions': io.evidence_ref(P / 'decisions.md', R), 'instruction': io.evidence_ref(P / 'instruction.md', R), 'dependency_public_input_identity': io.evidence_ref(D / 'consolidate-dependency-inventory-v2.json', R), 'submission': source, 'command_adaptation': 'Reproduce authored dbt seed/run/test. Public CSV and frozen dbt_utils copy steps are represented by byte-identical declared source payload, checked against actual task-specific image. No dbt deps command added; omission relative to instruction remains separately auditable. Fresh target/log path only. No database, target or gold imported.'}
for p, v in [(D / 'batch04-requests-v1.json', request), (D / 'batch04-input-review-v1.json', review)]:
    assert not p.exists()
    io.write_json(p, v)
print({'tasks': 1, 'trials': 2, 'files': len(source), 'request': str(D / 'batch04-requests-v1.json')})
