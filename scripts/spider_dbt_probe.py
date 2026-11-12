"""Run an unmodified Spider project once, without resolving dbt packages."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from context_probe import probe


def manifest(folder):
    out = {}
    if folder.exists():
        for p in sorted(folder.rglob('*')):
            if p.is_symlink():
                out[str(p.relative_to(folder))] = {'link': os.readlink(p)}
            elif p.is_file():
                out[str(p.relative_to(folder))] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                                                 'mode': p.stat().st_mode & 0o777}
    return out


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    out = Path('/logs/agent/unmodified-project')
    out.mkdir()
    project = Path('/workspace')
    before = manifest(project / 'dbt_packages')
    models = manifest(project / 'models')
    (out / 'packages-before.json').write_text(json.dumps(before, indent=2))
    (out / 'models-before.json').write_text(json.dumps(models, indent=2))
    context = probe(spec['database_paths'])
    command = ['dbt', 'run', '--profiles-dir', '/workspace', '--no-partial-parse']
    started = datetime.now(timezone.utc).isoformat()
    error = None
    with (out / 'dbt-run.log').open('w') as log:
        try:
            process = subprocess.run(command, cwd=project, stdout=log, stderr=subprocess.STDOUT, timeout=1500)
            code = process.returncode
        except subprocess.TimeoutExpired:
            code, error = None, 'dbt run exceeded the recorded 1500 second probe limit'
    finished = datetime.now(timezone.utc).isoformat()
    after = manifest(project / 'dbt_packages')
    models_after = manifest(project / 'models')
    (out / 'packages-after.json').write_text(json.dumps(after, indent=2))
    text = re.sub(r'\x1b\[[0-9;]*m', '', (out / 'dbt-run.log').read_text())
    missing = re.findall(r"depends on a node named ['\"]([^'\"]+)['\"][^\n]*not found", text)
    run_results = project / 'target/run_results.json'
    results = []
    if run_results.exists():
        # Retain errors/status only. A source-shipped target result is not a
        # new execution unless its generated_at falls in this run window.
        raw = json.loads(run_results.read_text())
        generated = raw.get('metadata', {}).get('generated_at')
        if generated and started <= generated <= finished:
            results = [{k: r.get(k) for k in ('unique_id', 'status', 'message', 'execution_time')}
                       for r in raw.get('results', [])]
    report = {'task_key': spec['task_key'], 'context': context, 'command': command,
        'started_at_utc': started, 'finished_at_utc': finished, 'exit_code': code, 'error': error,
        'vendored_packages_present': bool(before), 'packages_yml_present': (project / 'packages.yml').exists(),
        'packages_unchanged': before == after, 'model_sources_unchanged': models == models_after,
        'missing_model_dependencies_reported': missing, 'model_results': results,
        'diagnostic_lines': [line for line in text.splitlines() if any(t in line.lower() for t in
                             ('error', 'not found', 'could not', 'no such', 'does not exist', 'does not support'))],
        'dbt_deps_executed': False, 'model_requests': 0,
        'scope': 'One actual unmodified-project dbt run. A missing-model parse stop does not prove remaining models executable.'}
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('task_key', 'exit_code', 'missing_model_dependencies_reported',
                                           'packages_unchanged', 'model_sources_unchanged')}))


if __name__ == '__main__':
    main()
