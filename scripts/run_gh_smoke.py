#!/usr/bin/env python3
"""One allocated GPU, one pinned server, one native Harbor job; no agent loop."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from dbt_bench.io import digest, evidence_ref, file_hash, read_json, write_json
from dbt_bench.prepare import check_harbor, emit_config
from dbt_bench.trace import inspect_trial_trace
from dbt_bench.submission import bind_submission
from dbt_bench.report import inspect_submission
from dbt_bench.catalog import load_catalog
from submission_declarations import bind_versioned_submission


def prepare_smoke_task(root, spec, requested, invocation, out, *, submission_path=None):
    """Validate the selected task and capture contract before loading GPU weights."""
    key = requested or spec['task_key']
    row = next((row for row in load_catalog(root) if row['task_key'] == key), None)
    if row is None:
        raise ValueError('Unknown smoke task: ' + key)
    source = row['source']
    # Refuse stale/missing capture declarations before generating a runnable job.
    capture_config = {}
    if submission_path is None:
        submission_spec, submission_binding = bind_submission(root, capture_config, key)
    else:
        submission_spec, submission_binding = bind_versioned_submission(root, capture_config, key, submission_path)
    path = emit_config(root, source, 'openai/' + spec['served_model_name'], 'http://127.0.0.1:8000/v1',
                       spec['model'] + '@' + spec['revision'], output=out/'base-harbor.yaml',
                       tasks=[key], backend='apptainer')
    config = read_json(path)
    manifest = read_json(path.with_suffix('.manifest.json'))
    config['agents'][0]['kwargs']['config'] = read_json(root/'inference/qwen-code-settings.json')
    config['artifacts'] = capture_config['artifacts']
    config['job_name'] = source + '-qwen-gh-dev-' + invocation
    config = check_harbor(config)
    write_json(out/'submission-spec.json', submission_spec)
    submission_binding['spec'] = evidence_ref(out/'submission-spec.json', root)
    manifest['identity']['submission_capture'] = submission_binding
    manifest['identity']['smoke_task_selection'] = {'task_key':key, 'source':source,
        'serving_spec_default_task':spec['task_key'], 'explicit_override':requested is not None}
    write_json(out/'prepared-harbor.yaml', config)
    write_json(out/'prepared-harbor.manifest.json', manifest)
    return config, manifest, submission_spec


def inference_environment(root, inherited):
    settings = read_json(root / 'inference/runtime-env.json')
    # FlashInfer retains build.ninja across launches, including its toolkit paths.
    # A changed runtime/toolkit must never reuse the previous generated commands.
    build_identity = digest({'settings': settings, 'runtime_lock': file_hash(root/'inference/uv.lock')})
    return {**inherited, 'CC':settings['cc'], 'CXX':settings['cxx'],
            'CUDA_HOME':settings['cuda_home'], 'CUDA_PATH':settings['cuda_home'],
            'PATH':os.pathsep.join((str(root / 'inference/.venv/bin'),
                                   str(Path(settings['cuda_home']) / 'bin'), inherited.get('PATH', ''))),
            'TRITON_CACHE_DIR':str(root / settings['triton_cache']),
            'FLASHINFER_WORKSPACE_BASE':str(root / settings['flashinfer_cache'] / build_identity)}


def stop(process):
    if process and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def model_events(trial_dir):
    """A created session alone is not evidence that the model actually responded."""
    count, errors = 0, []
    for path in sorted(Path(trial_dir).glob("agent/qwen-sessions/**/*.jsonl")):
        for index, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("Non-object session event")
                if event.get("type") == "assistant":
                    parts = (event.get("message") or {}).get("parts", [])
                    if any(isinstance(p, dict) and (p.get("text") or p.get("functionCall")) for p in parts):
                        count += 1
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append({"path": str(path), "line": index, "error": str(exc)})
    return {"assistant_events": count, "parse_errors": errors}


def main():
    job = os.environ.get("SLURM_JOB_ID")
    if not job or os.environ.get("SLURM_JOB_PARTITION") != "gh-dev":
        raise RuntimeError("Run through the gh-dev Slurm script on an allocated node")
    attempt = os.environ.get('DBT_BENCH_SMOKE_ATTEMPT', '0')
    if not attempt.isdecimal():
        raise ValueError('DBT_BENCH_SMOKE_ATTEMPT must be a nonnegative integer')
    invocation = job if int(attempt) == 0 else job + '-attempt-' + str(int(attempt))
    out = ROOT / "artifacts/slurm" / invocation
    out.mkdir(parents=True, exist_ok=True)
    status_path = out / "status.json"
    if status_path.exists():
        raise ValueError("Do not overwrite an earlier smoke invocation")
    status = {"slurm_job_id": job, "invocation_id":invocation, "startup_attempt":int(attempt),
              "hostname": socket.gethostname(), "started_at": time.time(),
              "qwen_smoke_executed": False, "steps": [], "complete": False}
    server = None
    server_log = None
    job_name = None
    shutil.copyfile(Path(__file__), out/'runner.py')

    def save():
        write_json(status_path, status)

    def run(name, command, timeout, *, check=True, process_env=None):
        step = {"name": name, "argv": [str(c) for c in command], "started_at": time.time()}
        status["steps"].append(step)
        save()
        print(f"[{name}] starting", flush=True)
        with (out / f"{name}.log").open("w") as log:
            process = subprocess.Popen(step["argv"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True, env=process_env)
            try:
                step["returncode"] = process.wait(timeout=timeout)
            finally:
                stop(process)
                step["finished_at"] = time.time()
                step["log"] = evidence_ref(out / f"{name}.log", ROOT)
                save()
        print(f"[{name}] exit={step['returncode']}", flush=True)
        if check and step["returncode"]:
            raise RuntimeError(f"{name} failed; see {out / (name + '.log')}")
        return step["returncode"]

    def interrupted(signum, frame):
        raise RuntimeError(f"Slurm/process signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    save()
    try:
        run("gpu", ["nvidia-smi", "-q"], 30)
        run("allocation", ["scontrol", "show", "job", job], 30)
        spec_path = ROOT / "inference/qwen3.6-35b-a3b.json"
        spec = read_json(spec_path)
        run("harbor-patches", [sys.executable, ROOT / "scripts/apply_runtime_patches.py"], 60)
        run('harbor-config', [sys.executable, ROOT/'scripts/check_runtime.py', '--require-locked-installation',
                            '--output', out/'harbor-conformance.json'], 120)
        config, manifest, submission_spec = prepare_smoke_task(
            ROOT, spec, os.environ.get('DBT_BENCH_SMOKE_TASK'), invocation, out,
            submission_path=os.environ.get('DBT_BENCH_SUBMISSION_SPEC'))
        job_name = config['job_name']
        status['task_selection'] = manifest['identity']['smoke_task_selection']
        save()
        run("runtime-sync", ["uv", "sync", "--project", "inference", "--locked", "--no-build"], 1800)
        model_manifest = ROOT / "artifacts/inference/model.json"
        if not model_manifest.exists():
            run("prefetch-model", [sys.executable, ROOT / "scripts/prefetch_smoke_model.py"], 2400)
        model = read_json(model_manifest)
        if not model["verified"] or model["revision"] != spec["revision"] or model["spec_sha256"] != file_hash(spec_path):
            raise ValueError("Model inventory does not match the locked serving configuration")
        snapshot = Path(model["snapshot"])
        for item in model["files"]:
            if (snapshot / item["path"]).stat().st_size != item["size"]:
                raise ValueError("Verified model cache changed")
        run("torch", [ROOT / "inference/.venv/bin/python", "-c",
            "import torch, vllm, json; print(json.dumps(dict(torch=torch.__version__, "
            "vllm=vllm.__version__, cuda=torch.version.cuda, available=torch.cuda.is_available(), "
            "gpu=torch.cuda.get_device_name(0), capability=torch.cuda.get_device_capability(0)))); "
            "x=torch.ones(16,device='cuda'); print(x.sum().item())"], 120)
        server_env = inference_environment(ROOT, os.environ)
        run('compiler', [ROOT / 'inference/.venv/bin/python', ROOT / 'scripts/check_inference_compiler.py'],
            120, process_env=server_env)
        run('cuda-link', [ROOT / 'inference/.venv/bin/python', ROOT / 'scripts/check_inference_cuda.py'],
            120, process_env=server_env)
        run('model-import', [ROOT / 'inference/.venv/bin/python', '-c',
            'import triton; from vllm.model_executor.models.qwen3_5 import Qwen3_5MoeForConditionalGeneration; '
            'print(Qwen3_5MoeForConditionalGeneration.__name__); '
            'print(triton.runtime.driver.active.get_current_target())'], 180, process_env=server_env)
        run('flashinfer-gdn', [ROOT/'inference/.venv/bin/python', ROOT/'scripts/check_inference_flashinfer.py'],
            900, process_env=server_env)
        command = [str(ROOT / "inference/.venv/bin/vllm"), "serve", str(snapshot),
                   "--served-model-name", spec["served_model_name"], *spec["server_args"],
                   "--override-generation-config", json.dumps(spec["generation_defaults"])]
        write_json(out / "server-command.json", {"argv": command, "model": evidence_ref(model_manifest, ROOT),
                   "runtime_lock": evidence_ref(ROOT / "inference/uv.lock", ROOT),
                   "settings": evidence_ref(spec_path, ROOT),
                   "runtime_environment": evidence_ref(ROOT / 'inference/runtime-env.json', ROOT),
                   "controlled_environment": {k:server_env[k] for k in ('CC','CXX','PATH','CUDA_HOME','CUDA_PATH','TRITON_CACHE_DIR','FLASHINFER_WORKSPACE_BASE')},
                   "compiler_preflight": evidence_ref(out / 'compiler.log', ROOT),
                   "cuda_link_preflight": evidence_ref(out / 'cuda-link.log', ROOT),
                   "flashinfer_gdn_preflight": evidence_ref(out / 'flashinfer-gdn.log', ROOT),
                   "model_import_preflight": evidence_ref(out / 'model-import.log', ROOT)})
        server_log = (out / "vllm.log").open("w")
        server = subprocess.Popen(command, stdout=server_log, stderr=subprocess.STDOUT, cwd=ROOT,
                                  start_new_session=True, env=server_env)
        status["server_pid"] = server.pid
        save()
        endpoint = "http://127.0.0.1:8000"
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError(f"vLLM exited {server.returncode}; inspect vllm.log")
            try:
                with urllib.request.urlopen(endpoint + "/v1/models", timeout=5) as response:
                    models = json.load(response)
                if spec["served_model_name"] not in [m["id"] for m in models["data"]]:
                    raise ValueError("Unexpected endpoint model")
                write_json(out / "models.json", models)
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(5)
        else:
            raise TimeoutError("vLLM readiness exceeded 30 minutes")
        print("[vllm] ready", flush=True)
        status["server_ready_at"] = time.time()
        save()
        # Protocol smoke has explicit request/response evidence and is not a benchmark.
        request = {"model": spec["served_model_name"], "messages": [
            {"role": "user", "content": "Call emit_ping with value pong. Do not answer in prose."}],
            "tools": [{"type": "function", "function": {"name": "emit_ping", "description": "Emit a ping value",
                "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                               "required": ["value"]}}}], "tool_choice": "auto", "max_tokens": 2048}
        write_json(out / "protocol-request.json", request)
        req = urllib.request.Request(endpoint + "/v1/chat/completions", data=json.dumps(request).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=240) as response:
            result = json.load(response)
        write_json(out / "protocol-response.json", result)
        calls = result["choices"][0]["message"].get("tool_calls") or []
        if len(calls) != 1 or not calls[0].get("id") or calls[0]["function"]["name"] != "emit_ping" or json.loads(calls[0]["function"]["arguments"]) != {"value": "pong"}:
            raise ValueError("Model tool-call protocol smoke failed")
        manifest["identity"]["config"] = config
        manifest["identity"]["generation"] = {"server": spec, "client": config["agents"][0]["kwargs"]["config"],
                                               "actual_request_capture": "native Qwen OpenAI request logs and server logs; completeness not certified"}
        manifest["identity"]["serving_evidence"] = evidence_ref(out / "server-command.json", ROOT)
        manifest["identity"]["slurm_job_id"] = job
        manifest["identity"]["smoke_invocation_id"] = invocation
        manifest['identity']['smoke_runner'] = evidence_ref(out/'runner.py', ROOT)
        manifest["experiment_id"] = digest(manifest["identity"])
        final_config = out / "harbor.yaml"
        write_json(final_config, config)
        manifest["config_hash"] = file_hash(final_config)
        write_json(out / "harbor.manifest.json", manifest)
        os.environ["OPENAI_API_KEY"] = "local-no-auth"
        status["harbor_invoked_at"] = time.time()
        save()
        harbor_exit = run("harbor", [ROOT / "runtime/.venv/bin/harbor", "run", "-c", final_config], 6900, check=False)
        results = []
        for path in sorted((ROOT / "jobs" / config["job_name"]).glob("*/result.json")):
            data = read_json(path)
            agent = data.get("agent_result") or {}
            events = model_events(path.parent)
            capture = inspect_submission(path.parent, submission_spec)
            write_json(path.parent / 'submission-capture.json', capture)
            results.append({"result": evidence_ref(path, ROOT), "id": data["id"], "model_events": events,
                            "rewards": (data.get("verifier_result") or {}).get("rewards"),
                            "exception_info": data.get("exception_info"), "agent_result": agent,
                            "submission_capture": capture,
                            "submission_receipt": evidence_ref(path.parent / 'submission-capture.json', ROOT),
                            "trace_quality": inspect_trial_trace(path.parent, root=ROOT)})
            if events["assistant_events"]:
                status["qwen_smoke_executed"] = True
        status["trials"] = results
        status["complete"] = (harbor_exit == 0 and len(results) == 1 and status["qwen_smoke_executed"]
                              and results[0]["exception_info"] is None and results[0]["rewards"] is not None
                              and results[0]['submission_capture']['complete'])
        print(json.dumps({"complete": status["complete"], "qwen_smoke_executed": status["qwen_smoke_executed"],
                          "output": str(out)}, indent=2), flush=True)
        return 0 if status["complete"] else 1
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
        print(status["error"], flush=True)
        return 1
    finally:
        stop(server)
        if server_log:
            server_log.close()
        # Even interrupted/failed Harbor invocations retain evidence of actual model responses.
        raw_job = ROOT / "jobs" / job_name if job_name else None
        if raw_job is not None and raw_job.is_dir():
            status["retained_sessions"] = [{"trial_dir": str(p.relative_to(ROOT)), **model_events(p)}
                                           for p in sorted(raw_job.iterdir()) if p.is_dir()]
            status["qwen_smoke_executed"] = any(e["assistant_events"] for e in status["retained_sessions"])
        status["finished_at"] = time.time()
        save()


if __name__ == "__main__":
    raise SystemExit(main())
