#!/usr/bin/env python3
"""Download one pinned public smoke model and verify every repository file."""
import fcntl
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dbt_bench.io import file_hash, write_json
from huggingface_hub import HfApi, snapshot_download
from dbt_bench.local_config import required


def main():
    spec_path = ROOT / "inference/qwen3.6-35b-a3b.json"
    spec = json.loads(spec_path.read_text())
    cache = Path(required()["cache_root"]) / "huggingface/hub"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache.parent / "prefetch.lock").open("a") as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX)
        info = HfApi(token=False).model_info(spec["model"], revision=spec["revision"], files_metadata=True)
        if info.sha != spec["revision"]:
            raise ValueError("Model revision mismatch")
        snapshot = Path(snapshot_download(spec["model"], revision=spec["revision"],
                                         cache_dir=cache, token=False, max_workers=4))
        files = []
        for item in info.siblings:
            path = snapshot / item.rfilename
            if item.lfs:
                expected = item.lfs.sha256
                actual = file_hash(path)
                algorithm = "sha256"
            else:
                data = path.read_bytes()
                actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
                expected = item.blob_id
                algorithm = "git_blob_sha1"
            if path.stat().st_size != item.size or actual != expected:
                raise ValueError(f"Model file mismatch: {item.rfilename}")
            files.append({"path": item.rfilename, "size": item.size, algorithm: actual})
            print(f"verified {item.rfilename}", flush=True)
        write_json(Path(required()["artifact_root"]) / "inference/model.json", {
            "model": spec["model"], "revision": info.sha, "snapshot": str(snapshot),
            "spec_sha256": file_hash(spec_path), "files": files, "verified": True})
        print(json.dumps({"snapshot": str(snapshot), "verified_files": len(files)}), flush=True)


if __name__ == "__main__":
    main()
