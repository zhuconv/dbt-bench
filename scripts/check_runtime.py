#!/usr/bin/env python3
"""Inspect the installed pinned Harbor schema/adapter and save real conformance results.

This does not start Docker or call a model. It is expected to fail until the runtime is installed.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import argparse
import importlib.metadata
import inspect
import json

from dbt_bench.io import file_hash, read_json, write_json
from dbt_bench.prepare import bind_qwen_bundle, check_harbor
from dbt_bench.trace import audit_trace, harbor_schema_validator


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=Path("artifacts/runtime/conformance.json"))
    p.add_argument("--task", type=Path, action="append", default=[], help="Also load a prepared native task through Harbor")
    p.add_argument('--require-locked-installation', action='store_true', help='Also require the prepared fixed Qwen bundle to be present and verified')
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = {"schema_checked": False, "agent_factory_checked": False,
              "adapter_conformance": "pending", "qwen_smoke": False}
    try:
        from harbor.agents.installed.qwen_code import QwenCode
        from harbor.agents.factory import AgentFactory
        from harbor.models.job.config import JobConfig
        result["harbor_version"] = importlib.metadata.version("harbor")
        if result["harbor_version"] != "0.25.0":
            raise ValueError("Harbor version mismatch")
        result["configured_agents"] = []
        for name in ("ade", "spider", "deb", "validation"):
            config = check_harbor(read_json(root / "configs" / f"{name}.yaml"))
            for agent_config in JobConfig.model_validate(config).agents:
                cls = AgentFactory.get_agent_class_from_config(agent_config)
                if name != 'validation' and cls is not QwenCode:
                    raise ValueError(f'{name} must resolve to the pinned native QwenCode adapter')
                result["configured_agents"].append({'config':name, 'name':agent_config.name,
                                                    'class':cls.__module__+'.'+cls.__name__})
        result["schema_checked"] = True
        result["agent_factory_checked"] = True
        result["tasks"] = []
        for task in args.task:
            try:
                check_harbor(task_paths=[task])
                result["tasks"].append({"path": str(task), "loaded": True})
            except Exception as exc:
                result["tasks"].append({"path": str(task), "loaded": False, "error": str(exc)})
        source = Path(inspect.getfile(QwenCode))
        result["adapter_source_sha256"] = file_hash(source)
        if read_json(root / 'sources.lock.yaml')['qwen_code'].get('installation_bundle'):
            if not {'installation_bundle', 'installation_bundle_sha256'} <= set(QwenCode.options_model.model_fields):
                raise ValueError('Installed native adapter lacks the locked installation options')
            pin = read_json(root / 'sources.lock.yaml')['qwen_code']['installation_bundle']
            if args.require_locked_installation or (root / pin['default_manifest']).exists():
                bundle_config = read_json(root / 'configs/deb.yaml')
                result['locked_installation'] = bind_qwen_bundle(root, bundle_config)
                check_harbor(bundle_config)
            else:
                result['locked_installation'] = {'status': 'pending_bundle_preparation', 'adapter_options_present': True}
        # Verify native install really interpolates the requested version.
        install_source = inspect.getsource(QwenCode.install)
        result["native_version_pinning_source_present"] = "self._version" in install_source
        agent = object.__new__(QwenCode)
        agent.model_name = "openai/fixture"
        fixture = read_json(root / "tests/trace_fidelity/error-only.json")
        converted = agent._convert_events_to_trajectory(fixture["events"])
        if converted is None:
            raise ValueError("Pinned converter returned no trajectory")
        atif = converted.model_dump(mode="json")
        result["error_only_fixture"] = audit_trace(fixture["events"], atif, schema_validator=harbor_schema_validator)
        result["adapter_conformance"] = "pass_error_fixture_only" if result["error_only_fixture"]["content_fidelity"] == "complete" else "failed_error_fixture"
    except (ImportError, ValueError, AttributeError, OSError) as exc:
        result["error"] = str(exc)
    write_json(args.output, result)
    print(json.dumps(result, indent=2))
    return 0 if (result["agent_factory_checked"] and result["adapter_conformance"] == "pass_error_fixture_only"
                 and all(t["loaded"] for t in result.get("tasks", []))) else 1


if __name__ == "__main__":
    raise SystemExit(main())
