#!/usr/bin/env python3
"""Build an isolated patched Harbor; never modify the existing runtime or locks."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench import io


def prepare(output):
    import harbor
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new immutable runtime directory')
    if not output.is_relative_to('/scratch'):
        raise ValueError('Runtime payload belongs on SCRATCH')
    source = Path(harbor.__file__).parent.resolve()
    metadata = ROOT / 'patches/harbor-025-apptainer-timeout-v2/metadata.json'
    patch = io.read_json(metadata)
    if importlib.metadata.version('harbor') != patch['package_version']:
        raise ValueError('Wrong base Harbor version')
    replacements = {}
    for entry in patch['files']:
        path = source / entry['path']
        if io.file_hash(path) != entry['before_sha256']:
            raise ValueError('Patch preimage differs: ' + str(path))
        text = path.read_text()
        for change in entry['replacements']:
            if text.count(change['before_text']) != 1:
                raise ValueError('Ambiguous patch context')
            text = text.replace(change['before_text'], change['after_text'], 1)
        if hashlib.sha256(text.encode()).hexdigest() != entry['after_sha256']:
            raise ValueError('Patch postimage differs')
        compile(text, entry['path'], 'exec')
        replacements[entry['path']] = text
    before = [{'path': str(p.relative_to(source)), 'sha256': io.file_hash(p)}
              for p in sorted(source.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    output.mkdir(parents=True)
    venv.EnvBuilder(with_pip=False, symlinks=True).create(output / 'venv')
    site = output / 'venv/lib' / ('python' + '.'.join(map(str, sys.version_info[:2]))) / 'site-packages'
    destination = site / 'harbor'
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name, text in replacements.items():
        (destination / name).write_text(text)
    # The new site-packages precedes the sealed base. Only dependencies are
    # shared; both `python` imports and CLI use this independent Harbor copy.
    (site / 'sealed-base-dependencies.pth').write_text(str(source.parent) + '\n')
    binary = output / 'venv/bin/harbor'
    binary.write_text('#!' + str(output / 'venv/bin/python') + '\nfrom harbor.cli.main import app\napp()\n')
    binary.chmod(0o755)
    after = [{'path': r['path'], 'sha256': io.file_hash(destination / r['path'])} for r in before]
    changed = [r['path'] for r, z in zip(before, after) if r != z]
    if set(changed) != set(replacements):
        raise ValueError('Unexpected files changed in isolated package')
    if any(io.file_hash(source / row['path']) != row['sha256'] for row in before):
        raise ValueError('Base package changed during preparation')
    io.write_json(output / 'base-package-files.json', before)
    io.write_json(output / 'package-files.json', after)
    distributions = sorted([{'name': d.metadata['Name'], 'version': d.version} for d in importlib.metadata.distributions()], key=lambda r:r['name'].lower())
    io.write_json(output / 'shared-dependencies.json', {'site_packages': str(source.parent), 'distributions': distributions,
        'policy': 'Dependency base must remain sealed; runtime does not authorize upgrading it.'})
    manifest = {'schema_version':1, 'runtime_version':patch['runtime_version'], 'patch':io.evidence_ref(metadata, ROOT),
        'base_package':str(source), 'package':str(destination), 'python':str(output / 'venv/bin/python'),
        'harbor':str(binary), 'base_interpreter':io.evidence_ref(Path(sys.executable).resolve(), ROOT),
        'preparer':io.evidence_ref(Path(__file__), ROOT), 'base_files':io.evidence_ref(output / 'base-package-files.json', ROOT),
        'files':io.evidence_ref(output / 'package-files.json', ROOT), 'dependencies':io.evidence_ref(output / 'shared-dependencies.json', ROOT),
        'source_lock':io.evidence_ref(ROOT / 'sources.lock.yaml', ROOT), 'changed_files':changed,
        'base_untouched':True, 'model_requests':0, 'deployed_to_measurement':False}
    manifest['runtime_id'] = io.digest(manifest)
    io.write_json(output / 'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.output), indent=2))
