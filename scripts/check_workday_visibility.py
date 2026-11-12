#!/usr/bin/env python3
"""Scan the initial Workday task via locked Qwen shell service; no model request."""
import argparse
import asyncio
from datetime import datetime, timezone
import importlib
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import binding_identity, harbor_process_env, resolve_task
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, file_hash, read_json, write_json
from visibility_scan import fingerprint


async def run(args):
    from harbor.agents.installed.qwen_code import QwenCode
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    key = 'spider2__workday002'
    row = next(r for r in load_catalog(ROOT) if r['task_key'] == key)
    task = Task(task_dir=resolve_task(ROOT, row, 'apptainer'))
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(Path(__file__), out / 'runner.py')
    shutil.copyfile(ROOT / 'scripts/visibility_scan.py', out / 'scanner.py')
    prior = read_json(ROOT / 'artifacts/execution/workday-qwen-production-v1/result.json')
    bundle = ROOT / prior['installation_bundle']['path']
    assert file_hash(bundle) == prior['installation_bundle']['sha256']
    hidden, sources = [], []
    for section in ['tests', 'solution']:
        for path in sorted((task.paths.task_dir / section).rglob('*')):
            if path.is_file() and path.stat().st_size:
                hidden.append(fingerprint(str(path.relative_to(task.paths.task_dir)), path.read_bytes()))
                sources.append(evidence_ref(path, ROOT))
    reference = ROOT / 'validation/spider2/workday002/cases/semantics-v1/original-reference/upstream-reference.sql'
    hidden.append(fingerprint('independent-reference/workday__job_overview.sql', reference.read_bytes()))
    sources.append(evidence_ref(reference, ROOT))
    spec = {'hidden_assets': hidden, 'target_relation': 'workday__job_overview', 'maximum_text_bytes': 2 * 1024 * 1024,
            'excluded_roots': ['/proc', '/sys', '/dev', '/logs'],
            'excluded_files': ['/tmp/workday-visibility-scan.py', '/tmp/workday-visibility-spec.json', '/tmp/workday-visibility-tool.mjs'],
            'declared_absent_paths': ['/tests', '/solution', '/solutions',
                '/workspace/models/workday__job_overview.sql'],
            'host_paths': [str(ROOT), str(ROOT.parent / 'training')],
            'databases': [{'path': '/workspace/workday.duckdb', 'absent_relations': [['main', 'workday__job_overview']]}]}
    spec['structured_artifacts'] = [{'path': project + '/' + name, 'kind': kind}
        for project in ['/workspace', '/opt/dbt-bench-initial']
        for name, kind in [('target/manifest.json', 'json'), ('target/run_results.json', 'json'),
                           ('target/partial_parse.msgpack', 'msgpack'), ('target/graph.gpickle', 'pickle-opcodes'),
                           ('logs/dbt.log', 'text-jsonlines'), ('logs/dbt.log.1', 'text-jsonlines')]]
    write_json(out / 'hidden-spec.json', spec)
    logger = logging.getLogger('workday-visibility')
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(out / 'environment.log')
    logger.addHandler(handler)
    paths = TrialPaths(trial_dir=out / 'environment')
    paths.mkdir()
    os.environ['PATH'] = harbor_process_env('apptainer')['PATH']
    env = SingularityEnvironment(environment_dir=task.paths.environment_dir, environment_name='workday-visibility',
        session_id=out.name, trial_paths=paths, task_env_config=task.config.environment,
        persistent_env={'DB_TYPE': 'duckdb'}, logger=logger, singularity_no_mount='home,tmp,cwd,hostfs,bind-paths')
    agent = QwenCode(logs_dir=paths.agent_dir, model_name='openai/visibility-no-model-request', version='0.26.5',
        logger=logger, installation_bundle=bundle, installation_bundle_sha256=file_hash(bundle))
    result = {'schema_version': 1, 'task_key': key, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'model_requests': 0, 'benchmark_trials': 0, 'passed': False, 'source_lock': evidence_ref(ROOT / 'sources.lock.yaml', ROOT),
        'backend_binding': binding_identity(ROOT, row, 'apptainer'), 'declared_hidden_sources': sources,
        'hidden_spec': evidence_ref(out / 'hidden-spec.json', ROOT), 'installation_bundle': evidence_ref(bundle, ROOT),
        'runner': evidence_ref(out / 'runner.py', ROOT), 'scanner': evidence_ref(out / 'scanner.py', ROOT),
        'scope': 'Initial image after locked production Qwen installation, through its actual pipe shell service. Declared hidden files and pinned independent reference SQL, explicit paths and output relation only; no general information-flow certification.'}
    package = Path(importlib.import_module('harbor').__file__).parent
    result['adapter_sources'] = [evidence_ref(package / p, ROOT) for p in ['agents/installed/qwen_code.py', 'agents/installed/base.py', 'agents/installed/node_install.py', 'environments/singularity/singularity.py']]
    async def execute(label, command, agent_context=False, timeout=240):
        execution = await (agent.exec_as_agent(env, command=command, timeout_sec=timeout) if agent_context else env.exec(command, timeout_sec=timeout))
        write_json(out / (label + '.json'), execution.model_dump(mode='json'))
        assert execution.return_code == 0, label + ': ' + str(execution.stderr)
        print(label + ': passed', flush=True)
        return execution
    try:
        await asyncio.wait_for(env.start(force_build=False), 300)
        if task.config.environment.healthcheck:
            await execute('healthcheck', task.config.environment.healthcheck.command)
        before = await execute('before-database-hash', 'sha256sum /workspace/workday.duckdb')
        await agent.setup(env)
        for source, dest in [(out / 'scanner.py', '/tmp/workday-visibility-scan.py'), (out / 'hidden-spec.json', '/tmp/workday-visibility-spec.json')]:
            await env.upload_file(source, dest)
        service = '/root/.nvm/versions/node/v22.23.4/lib/node_modules/@qwen-code/qwen-code/chunks/chunk-NMY3PYNO.js'
        tool = out / 'tool.mjs'
        tool.write_text('import { ShellExecutionService } from ' + json.dumps(service) + ';\n'
            'import {createHash} from "node:crypto"; import {readFileSync} from "node:fs";\n'
            'const command="python3 /tmp/workday-visibility-scan.py /tmp/workday-visibility-spec.json /logs/agent/visibility";\n'
            'const h=await ShellExecutionService.execute(command,"/workspace",()=>{},AbortSignal.timeout(600000),false,{},{});\n'
            'const r=await h.result; console.log(JSON.stringify({exit_code:r.exitCode,execution_method:r.executionMethod,'
            'service_sha256:createHash("sha256").update(readFileSync(' + json.dumps(service) + ')).digest("hex"),output:r.output}));\n'
            'if(r.exitCode!==0)process.exitCode=1;\n')
        await env.upload_file(tool, '/tmp/workday-visibility-tool.mjs')
        observation = await execute('native-tool-scan', '. ~/.nvm/nvm.sh; node /tmp/workday-visibility-tool.mjs', True, 660)
        payload = json.loads(observation.stdout)
        assert payload['exit_code'] == 0 and payload['service_sha256'] == '29bff9e8bec4d8222039490a179ea9f546caf4542fa47adab497b227bce1ef07'
        after = await execute('after-database-hash', 'sha256sum /workspace/workday.duckdb')
        assert before.stdout.split()[0] == after.stdout.split()[0]
        result['initial_database_unchanged'] = True
        await env.download_dir('/logs/agent/visibility', out / 'visibility')
        scan = read_json(out / 'visibility/scan.json')
        facts = read_json(out / 'visibility/facts.json')
        structured = read_json(out / 'visibility/structured.json')
        structured_problems = [r for r in structured if r.get('error') or r.get('result', {}).get('matches')
                               or r.get('result', {}).get('target_mentions') or r.get('result', {}).get('counts', {}).get('opaque_values')]
        result.update(scan_complete=not scan['errors'], observed_matches=scan['matches'], facts=facts,
            scan=evidence_ref(out / 'visibility/scan.json', ROOT), inventory=evidence_ref(out / 'visibility/files.jsonl', ROOT),
            observed_facts=evidence_ref(out / 'visibility/facts.json', ROOT), mounts=evidence_ref(out / 'visibility/mountinfo.txt', ROOT),
            structured=evidence_ref(out / 'visibility/structured.json', ROOT), structured_problems=structured_problems,
            tool=evidence_ref(tool, ROOT), service_sha256=payload['service_sha256'])
        result['passed'] = (not scan['errors'] and not scan['matches'] and all(facts['declared_absent_paths'].values())
                            and not structured_problems and not any(facts['host_paths_visible'].values())
                            and not any(r['observed'] for r in facts['database_relations']))
    except Exception as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}
    finally:
        try: await env.stop(delete=True)
        except Exception as exc: result.update(passed=False, cleanup_error=str(exc))
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
        result['environment_log'] = evidence_ref(out / 'environment.log', ROOT)
        result['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        write_json(out / 'result.json', result)
    print(json.dumps({'passed': result['passed'], 'matches': result.get('observed_matches'), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
