#!/usr/bin/env python3
"""Register explicitly reviewed native build adaptations, after real context checks."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import register_sif
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, file_hash, read_json, write_json


SPECS = {
    'ade__ade-bench-airbnb005': {
        'stem': 'ade-airbnb005', 'definition': 'ade-airbnb005',
        'context': 'ade-airbnb005-v1', 'visibility': 'ade-airbnb005-visibility-review',
        'extra_evidence': ['artifacts/images/ade-airbnb-data-pin.json', 'artifacts/images/node22-arm64-pin.json',
                           'artifacts/images/yq-arm64-pin.json', 'artifacts/images/python311-slim-pin.json',
                           'environments/python/ade/uv.lock', 'environments/apptainer/ade-requirements.txt'],
        'differences': [
            'Use pinned native Python OCI ARM64 digest, pinned official Node 22 ARM64 archive and yq ARM64 release instead of live apt NodeSource/latest amd64 yq.',
            'Native Google Drive database downloaded by its file ID, observed SHA256 retained; no published database checksum was provided.',
            'Pin full Python dependency resolution including the native exact dbt/DuckDB versions and intended gdown/pyyaml/uv constraints.',
            'XZ decoding uses Python lzma; tar ignores archive ownership unsupported by the root-mapped namespace.',
            'Execute native setup.sh and fail the build on setup errors instead of hiding its exit code behind cleanup.',
            'Move dbt_packages/target/logs into an initial snapshot and copy into writable tmpfs on container startup.',
            'APT package versions and build result are recorded; byte-identical Docker versus Apptainer parity has not been executed.',
            'Harbor server dependencies still installed dynamically in this v1 image; unsuitable for a final dependency-frozen pool.'
        ]},
    'spider2__workday002': {
        'stem': 'spider-workday002-v2', 'definition': 'spider-workday002-v2',
        'context': 'spider-workday002-v2', 'visibility': 'spider-workday002-v2-visibility',
        'extra_evidence': ['artifacts/images/python311-slim-pin.json', 'environments/python/spider/uv.lock',
                           'environments/apptainer/spider-requirements.txt', 'environments/python/harbor-server/uv.lock',
                           'environments/apptainer/harbor-server-requirements.txt'],
        'differences': [
            'Pin native Python base to its ARM64 digest and all Python dependencies by wheel hashes.',
            'Use dbt-core 1.10.22/dbt-duckdb 1.10.0 within native constraints; dbt-core 1.12.6 experimental parser lacked a CPython 3.11 ARM64 wheel.',
            'Install native Harbor bootstrap system Python/venv/tmux/asciinema during build; preinstall its isolated fastapi/uvicorn venv from a separate lock.',
            'Preserve native optional dbt deps behavior, project paths and full initial DuckDB file; gold and solution are not copied.',
            'Move generated dbt_packages/target/logs to an initial snapshot for writable initialization.',
            'APT package versions and build result are recorded; byte-identical Docker versus Apptainer parity has not been executed.'
        ]},
    'deb__dbt-consolidate': {
        'stem': 'deb-consolidate-env-v2', 'definition': 'deb-consolidate-env-v2',
        'context': 'deb-consolidate-v2', 'visibility': 'deb-consolidate-v2-visibility-review',
        'extra_evidence': ['artifacts/images/deb-consolidate-data-check.json', 'artifacts/images/deb-base-env-v2-initial-inspection.json',
                           'artifacts/execution/deb-base-initial-tree/result.json'],
        'differences': [
            'Pin native FROM image to the recorded ARM64 OCI digest; copy all three native data CSVs to /app/data with exact hash verification.',
            'Execute the native DuckDB entrypoint to establish its initialization marker; native compose overrides entrypoint, so this is an explicit backend adaptation.',
            'Initialize dbt_packages from a byte/mode-identical writable copy for dbt deps; actual Harbor exec preserves later deletion.',
            'Native tests/private_data files are identical to the three intentionally public input CSVs. Do not claim these are independent hidden test data.',
            'Harbor server bootstrap remains dynamically installed; full Docker versus Apptainer parity has not been executed.'
        ]}
}


def main():
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    for key, spec in SPECS.items():
        context = ROOT / f"artifacts/execution/{spec['context']}/result.json"
        assert read_json(context)['integration_passed'], key
        visibility = ROOT / f"artifacts/images/{spec['visibility']}.json"
        scan = read_json(visibility)
        assert not scan.get('unexplained_matches', scan.get('exact_matches')), key
        image = ROOT / f"artifacts/images/{spec['stem']}.sif"
        row = rows[key]
        refs = [f"environments/apptainer/{spec['definition']}.def",
                f"artifacts/images/{spec['stem']}-build.log", str(context), *spec['extra_evidence']]
        provenance = {'architecture': 'aarch64', 'native_task_hash': row['adapted_task_hash'],
            'dockerfile_sha256': file_hash(ROOT / row['run_path'] / 'environment/Dockerfile'),
            'image_sha256': file_hash(image), 'complete_native_build': True,
            'build_kind': 'reviewed_apptainer_definition', 'reviewed_differences': spec['differences'],
            'build_evidence': [evidence_ref(ROOT / p, ROOT) for p in refs],
            'agent_visibility_evidence': evidence_ref(visibility, ROOT),
            'scope': 'Task build/startup readiness only; no semantic or Qwen certification'}
        path = ROOT / f"artifacts/images/{spec['stem']}-provenance.json"
        write_json(path, provenance)
        print(register_sif(ROOT, row, image, path), flush=True)


if __name__ == '__main__':
    main()
