"""Backend discovery and immutable Apptainer task bindings using Harbor's native adapter."""
from __future__ import annotations

import os
import json
import re
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import tomllib

from .io import digest, evidence_ok, evidence_ref, file_hash, read_json, tree_hash, write_json

BACKENDS = ("docker", "apptainer")
TACC_APPTAINER = Path("/opt/apps/tacc-apptainer/1.4.1/bin/apptainer")
EXPECTED_APPTAINER_VERSION = "apptainer version 1.4.1-1.el9"


def find_apptainer():
    """Check PATH and the explicit TACC module location, without changing the caller shell."""
    for candidate in (shutil.which("apptainer"), shutil.which("singularity"), str(TACC_APPTAINER)):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate).absolute()
    return None


def runtime_info(backend):
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend: {backend}")
    binary = shutil.which("docker") if backend == "docker" else find_apptainer()
    result = {"backend": backend, "binary": str(binary) if binary else None,
              "architecture": platform.machine(), "available": False, "version": None}
    if not binary:
        result["reason"] = "Executable not found in PATH or supported cluster module location"
        return result
    try:
        p = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
        result.update(available=p.returncode == 0, version=p.stdout.strip(), error=p.stderr.strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = str(exc)
    return result


def require_runtime(backend):
    info = runtime_info(backend)
    if not info["available"]:
        raise RuntimeError(f"{backend} unavailable: {info.get('reason') or info.get('error')}")
    if backend == "apptainer" and info["version"] != EXPECTED_APPTAINER_VERSION:
        raise RuntimeError(f"Apptainer version differs from the TACC pin: {info['version']}")
    return info


def harbor_process_env(backend):
    env = dict(os.environ)
    if backend == "apptainer":
        binary = find_apptainer()
        if not binary:
            raise RuntimeError("Load module tacc-apptainer/1.4.1")
        # The pinned Harbor backend invokes the literal command 'singularity'.
        sibling = binary.parent / "singularity"
        if not sibling.is_file() and not shutil.which("singularity"):
            raise RuntimeError("Harbor 0.25.0 requires a singularity command alongside Apptainer")
        env["PATH"] = str(binary.parent) + os.pathsep + env.get("PATH", "")
    return env


def configure_backend(config, backend):
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend: {backend}")
    environment = config.setdefault("environment", {})
    environment["type"] = "docker" if backend == "docker" else "singularity"
    if backend == "apptainer":
        environment.setdefault("kwargs", {})["singularity_no_mount"] = "home,tmp,cwd,hostfs,bind-paths"
    return config


def binding_path(root, row):
    return Path(root) / "catalog/backends/apptainer" / f"{row['task_key']}.json"


def binding_identity(root, row, backend):
    if backend == "docker":
        return None
    path = binding_path(root, row)
    if not path.exists():
        return {"status": "missing_sif_binding"}
    binding = read_json(path)
    image = Path(root) / binding["image"]["path"]
    task = Path(root) / binding["run_path"]
    return {"binding_sha256": file_hash(path), "evidence_current": all(evidence_ok(ref, root) for ref in binding["evidence"]),
            "image_sha256": file_hash(image) if image.is_file() else None,
            "task_sha256": tree_hash(task) if task.is_dir() else None}


def resolve_task(root, row, backend):
    original = Path(root) / row["run_path"]
    if not row["adapted_task_hash"] or not original.is_dir() or tree_hash(original) != row["adapted_task_hash"]:
        raise ValueError(f"Task not prepared or modified: {row['task_key']}")
    if backend == "docker":
        return original
    if backend != "apptainer":
        raise ValueError(f"Unknown backend: {backend}")
    path = binding_path(root, row)
    if not path.exists():
        raise ValueError(f"Missing built task SIF binding for {row['task_key']}; use prepare sif after building the complete native environment")
    binding = read_json(path)
    if binding["native_task_hash"] != row["adapted_task_hash"]:
        raise ValueError("SIF was built for a different task snapshot")
    current = binding_identity(root, row, backend)
    if current["image_sha256"] != binding["image"]["sha256"] or current["task_sha256"] != binding["run_hash"]:
        raise ValueError("SIF or backend task content changed")
    if not all(evidence_ok(ref, root) for ref in binding["evidence"]):
        raise ValueError("SIF provenance/equivalence/visibility evidence missing or changed")
    if binding["architecture"] != platform.machine():
        raise ValueError("SIF provenance architecture differs from current host")
    return Path(root) / binding["run_path"]


def _set_image(text, image):
    """Change only environment.docker_image; reject ambiguous TOML rewrites."""
    before = tomllib.loads(text)
    section = re.search(r"(?m)^\[environment\][ \t]*(?:#.*)?$", text)
    assignment = "docker_image = " + json.dumps(str(image)) + "\n"
    if section:
        start = text.find("\n", section.end())
        start = len(text) if start == -1 else start + 1
        following = re.search(r"(?m)^\[", text[start:])
        end = start + following.start() if following else len(text)
        body = text[start:end]
        if "docker_image" in before.get("environment", {}):
            body, count = re.subn(r"(?m)^docker_image\s*=.*(?:\n|$)", assignment, body)
            if count != 1:
                raise ValueError("Cannot safely replace native docker_image declaration")
        else:
            body = assignment + body
        updated = text[:start].rstrip("\n") + "\n" + body + text[end:]
    else:
        updated = text.rstrip() + "\n\n[environment]\n" + assignment
    expected = {**before, "environment": {**before.get("environment", {}), "docker_image": str(image)}}
    if tomllib.loads(updated) != expected:
        raise ValueError("Backend image edit changed another task setting")
    return updated


def register_sif(root, row, image, provenance_path):
    """Bind a complete prebuilt task environment; never confuse a base image with its Dockerfile."""
    root, image = Path(root).resolve(), Path(image).resolve()
    native = resolve_task(root, row, "docker")
    if image.suffix != ".sif" or not image.is_file():
        raise ValueError("Provide an existing complete task .sif image")
    provenance = read_json(provenance_path)
    if provenance.get("native_task_hash") != row["adapted_task_hash"]:
        raise ValueError("Image provenance is for a different native task")
    if provenance.get("dockerfile_sha256") != file_hash(native / "environment/Dockerfile"):
        raise ValueError("Image provenance Dockerfile differs")
    if provenance.get("image_sha256") != file_hash(image):
        raise ValueError("SIF hash differs from provenance")
    if provenance.get("complete_native_build") is not True:
        raise ValueError("A base image is insufficient: complete Dockerfile COPY/RUN/setup effects must be built")
    if provenance.get("architecture") != platform.machine():
        raise ValueError("Image architecture differs from host; no implicit emulation")
    if provenance.get("build_kind") not in {"native_oci_image", "reviewed_apptainer_definition"}:
        raise ValueError("Declare native OCI build or reviewed Apptainer definition")
    refs = provenance.get("build_evidence", [])
    visibility = provenance.get("agent_visibility_evidence")
    if not refs or not evidence_ok(visibility, root) or not all(evidence_ok(ref, root) for ref in refs):
        raise ValueError("Complete build and hidden-material visibility evidence required")
    refs = [*refs, visibility, evidence_ref(provenance_path, root)]
    runtime = require_runtime("apptainer")
    inspected = subprocess.run([runtime["binary"], "inspect", "--json", str(image)],
                               capture_output=True, text=True, timeout=30)
    if inspected.returncode:
        raise ValueError(f"Apptainer cannot inspect this image: {inspected.stderr.strip()}")
    inspection = json.loads(inspected.stdout)
    identity = {"native_task_hash": row["adapted_task_hash"], "image_sha256": file_hash(image),
                "provenance_sha256": file_hash(provenance_path), "image_path": str(image)}
    destination = root / ".cache/backend-tasks/apptainer" / digest(identity) / row["task_key"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        staged = Path(temp) / row["task_key"]
        shutil.copytree(native, staged, symlinks=True)
        toml = staged / "task.toml"
        toml.write_text(_set_image(toml.read_text(), image))
        staged_hash = tree_hash(staged)
        if destination.exists():
            if tree_hash(destination) != staged_hash:
                raise ValueError("Backend task modified; refuse to overwrite")
        else:
            staged.rename(destination)
    binding = {"schema_version": 1, "task_key": row["task_key"], "backend": "apptainer",
               "harbor_environment_type": "singularity", "native_task_hash": row["adapted_task_hash"],
               "run_path": str(destination.relative_to(root)), "run_hash": staged_hash,
               "image": evidence_ref(image, root), "architecture": provenance["architecture"],
               "runtime_at_registration": runtime, "inspection": inspection, "evidence": refs,
               "adaptation": {"type": "environment", "changed_file": "task.toml", "changed_key": "environment.docker_image",
                              "before_sha256": file_hash(native / "task.toml"), "after_sha256": file_hash(destination / "task.toml")},
               "backend_validation": "pending_real_context_and_cross_backend_equivalence"}
    path = binding_path(root, row)
    if path.exists() and read_json(path) != binding:
        old = read_json(path)
        write_json(path.parent / "history" / f"{row['task_key']}-{digest(old)}.json", old)
    write_json(path, binding)
    return path
