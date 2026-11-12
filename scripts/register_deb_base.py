#!/usr/bin/env python3
"""Bind only reviewed FROM-only DEB tasks to their complete native base environment."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from dbt_bench.catalog import load_catalog
from dbt_bench.backends import register_sif
from dbt_bench.io import evidence_ref, file_hash, read_json, write_json


def main():
    image = ROOT / 'artifacts/images/deb-base-env-v2.sif'
    inspection = ROOT / 'artifacts/images/deb-base-env-v2-initial-inspection.json'
    context = ROOT / 'artifacts/execution/deb-base-env-v2/result.json'
    facts = read_json(inspection)
    assert facts['identical_initial_packages'] and not facts['hidden_asset_exact_byte_matches']
    assert read_json(context)['integration_passed']
    image_hash = file_hash(image)
    accepted, separate = [], []
    for row in load_catalog(ROOT):
        if row['source'] != 'deb':
            continue
        task = ROOT / row['run_path']
        dockerfile = task / 'environment/Dockerfile'
        lines = [l.strip() for l in dockerfile.read_text().splitlines() if l.strip() and not l.lstrip().startswith('#')]
        if not lines or lines[0] != 'FROM ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0' or any(
                l not in {'FROM ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0', 'WORKDIR /app'} for l in lines):
            separate.append(row['task_key'])
            continue
        provenance = {'architecture': facts['architecture'], 'native_task_hash': row['adapted_task_hash'],
                      'dockerfile_sha256': file_hash(dockerfile), 'image_sha256': image_hash,
                      'complete_native_build': True, 'build_kind': 'reviewed_apptainer_definition',
                      'reviewed_docker_instructions': lines,
                      'reason': 'No task COPY/RUN; the pinned native OCI base supplies the complete environment. Optional WORKDIR /app matches its Config.WorkingDir. Native ENTRYPOINT and writable initial packages are explicitly restored.',
                      'build_evidence': [evidence_ref(ROOT / p, ROOT) for p in [
                          'environments/apptainer/deb-base-env-v2.def', 'artifacts/images/deb-base-arm64.json',
                          'artifacts/images/deb-base-env-v2-build.log', 'artifacts/images/deb-base-env-v2-initial-inspection.json',
                          'artifacts/execution/deb-base-env-v2/result.json', 'patches/harbor-025-apptainer-exec-v1/metadata.json']],
                      'agent_visibility_evidence': evidence_ref(inspection, ROOT),
                      'scope': 'Environment build effects; per-task positive/nop/semantic validation remains independent'}
        path = ROOT / 'artifacts/images/deb-base-provenance' / (row['task_key'] + '.json')
        write_json(path, provenance)
        register_sif(ROOT, row, image, path)
        accepted.append(row['task_key'])
        print('bound ' + row['task_key'], flush=True)
    write_json(ROOT / 'artifacts/images/deb-base-bindings.json', {'bound': accepted, 'separate_task_builds': separate,
                                                                'image': evidence_ref(image, ROOT)})
    print(f'Bound {len(accepted)}; separate recipes required for {separate}', flush=True)


if __name__ == '__main__':
    main()
