#!/usr/bin/env python3
"""Build a deterministic native-layout Qwen installation from verified locks."""
from pathlib import Path
import argparse
import gzip
import importlib.util
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from dbt_bench.io import evidence_ref,file_hash,read_json,write_json
from scripts.freeze_qwen_dependencies import package_inventory

spec=importlib.util.spec_from_file_location('qwen_bundle_install',ROOT/'runtime/qwen-code/install_bundle.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


def verify_modules(installation, receipt):
    directory=installation/'first'
    modules=directory/'node_modules'
    installer.inventory(modules)  # Reject escaping links and special files.
    actual=[{'path':str(p.relative_to(directory)),'sha256':file_hash(p),'size':p.stat().st_size}
            for p in sorted(modules.rglob('*')) if p.is_file()]
    if actual!=read_json(installation/'first-inventory.json'):
        raise ValueError('Prepared Qwen file inventory changed')
    canonical,_=package_inventory(actual)
    expected={(r['package'],r['path']):(r['sha256'],r['size']) for r in receipt['package_files']}
    if canonical!=expected:
        raise ValueError('Prepared Qwen package bytes differ from locked native receipt')


def build(installation, out):
    installation=Path(installation).resolve();out=Path(out).resolve()
    report=read_json(installation/'result.json')
    if not report.get('passed') or not report.get('matches_native_package_files'):
        raise ValueError('Require a successful locked native-byte comparison')
    locked=ROOT/'runtime/qwen-code'
    for item in report['locks']:
        if file_hash(ROOT/item['path']) != item['sha256']:
            raise ValueError('Installation locks changed')
    verify_modules(installation,read_json(locked/'native-installation.json'))
    node=read_json(locked/'node.lock.json');nvm=read_json(locked/'nvm.lock.json')
    archive=ROOT/'.cache/qwen-runtime'/Path(node['url']).name
    if file_hash(archive)!=node['archive_sha256']:raise ValueError('Node archive changed')
    out.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(Path(__file__),out/'builder.py')
    shutil.copyfile(locked/'install_bundle.py',out/'install_bundle.py')
    cache=ROOT/'.cache/qwen-runtime'/('nvm-'+nvm['revision']);cache.mkdir(parents=True,exist_ok=True)
    for entry in nvm['files']:
        path=cache/entry['path']
        if not path.exists():
            with urllib.request.urlopen(entry['url'],timeout=120) as response:path.write_bytes(response.read())
        if file_hash(path)!=entry['sha256']:raise ValueError('NVM support file checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='qwen-bundle-',dir=out) as temp:
        stage=Path(temp);tree=stage/'nvm';tree.mkdir()
        for entry in nvm['files']:
            path=tree/entry['path'];shutil.copyfile(cache/entry['path'],path);path.chmod(int(entry['mode'],8))
        (tree/'alias').mkdir();(tree/'alias/default').write_text('v'+node['version']+'\n')
        version=tree/'versions/node'/('v'+node['version']);version.parent.mkdir(parents=True)
        with tarfile.open(archive,'r:xz') as stream:stream.extractall(stage/'node-source',filter='data')
        shutil.move(stage/'node-source'/('node-v'+node['version']+'-linux-arm64'),version)
        if file_hash(version/'bin/node')!=node['node_binary_sha256']:raise ValueError('Node binary differs')
        modules=version/'lib/node_modules'
        for source in (installation/'first/node_modules').iterdir():
            if source.name in ('.bin','.package-lock.json'):continue
            target=modules/source.name
            if target.exists():raise ValueError('Unexpected native Node package collision')
            shutil.copytree(source,target,symlinks=True)
        package=read_json(modules/'@qwen-code/qwen-code/package.json')
        if package['version']!='0.26.5':raise ValueError('Wrong Qwen version')
        target=modules/'@qwen-code/qwen-code'/package['bin']['qwen']
        if not target.resolve().is_relative_to(modules) or not target.is_file():raise ValueError('Invalid Qwen executable')
        (version/'bin/qwen').symlink_to(os.path.relpath(target,version/'bin'))
        entries=installer.inventory(tree);write_json(out/'inventory.json',entries)
        # Normalize archive timestamps/owners, retain file modes and safe symlinks.
        def normalized(info):
            info.uid=info.gid=info.mtime=0;info.uname=info.gname='';info.pax_headers={}
            return info
        with (out/'bundle.tar.gz').open('wb') as raw:
            with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=6) as zipped:
                with tarfile.open(fileobj=zipped,mode='w',dereference=False) as stream:
                    stream.add(tree,arcname='nvm',filter=normalized)
    manifest={'schema_version':1,'kind':'dbt-bench-qwen-bundle-v1','platform':'linux-arm64',
        'qwen_version':'0.26.5','node_version':node['version'],'npm_version':node['npm_version'],
        'nvm_revision':nvm['revision'],'entries':len(entries),
        'files':{name:{'path':filename,'sha256':file_hash(out/filename)} for name,filename in
                 [('archive','bundle.tar.gz'),('inventory','inventory.json'),('installer','install_bundle.py')]},
        'locks':[evidence_ref(locked/name,ROOT) for name in ['node.lock.json','nvm.lock.json','package-lock.json','native-installation.json']],
        'builder':evidence_ref(out/'builder.py',ROOT),
        'package_comparison':evidence_ref(ROOT/'scripts/freeze_qwen_dependencies.py',ROOT),
        'source_installation':evidence_ref(installation/'result.json',ROOT),
        'scope':'Fixed native-compatible install layout; Qwen runtime behavior and task semantics are separately validated'}
    write_json(out/'bundle.json',manifest)
    print(json.dumps({'manifest':str(out/'bundle.json'),'sha256':file_hash(out/'bundle.json'),'entries':len(entries)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installation',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();build(args.installation,args.output_dir)
