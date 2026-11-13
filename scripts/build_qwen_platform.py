#!/usr/bin/env python3
"""Assemble a pinned x64 Qwen bundle from upstream integrity-checked tarballs.

Cross assembly never executes foreign code. On the target host, the native
adapter additionally executes Node/npm/Qwen version checks before any attempt.
"""
import argparse
import base64
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from build_qwen_bundle import normalize_runtime_modes


def fetch(url, dest, expected, algorithm='sha256'):
    if not dest.exists():
        dest.parent.mkdir(parents=True,exist_ok=True)
        with urllib.request.urlopen(url,timeout=120) as response,dest.with_suffix('.partial').open('wb') as stream:
            shutil.copyfileobj(response,stream)
        dest.with_suffix('.partial').replace(dest)
    h=hashlib.new(algorithm)
    with dest.open('rb') as stream:
        for b in iter(lambda:stream.read(1024*1024),b''):h.update(b)
    if h.digest()!=expected:raise ValueError('Upstream integrity mismatch: '+url)
    return dest


def selected(packages, cpu):
    result={}
    for name,v in packages.items():
        if not name:continue
        if any(v.get(k) and value not in v[k] for k,value in [('cpu',cpu),('os','linux'),('libc','glibc')]):continue
        if not name.startswith('node_modules/') or '..' in Path(name).parts:raise ValueError('Unexpected npm layout')
        result[name]=v
    return result


def build(output,cache,*,locked=None,require_pin=True):
    locked=locked or ROOT/'runtime/qwen-code/linux-x64';out=Path(output).resolve();cache=Path(cache).resolve()
    if out.exists():raise ValueError('Use a new output directory')
    out.mkdir(parents=True);node=io.read_json(locked/'node.lock.json');nvm=io.read_json(locked/'nvm.lock.json')
    npm=io.read_json(locked/'package-lock.json');cpu=node['platform'].removeprefix('linux-')
    spec=importlib.util.spec_from_file_location('bundle_installer',locked/'install_bundle.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    entries=selected(npm['packages'],cpu);archives=[]
    for name,v in entries.items():
        algorithm,value=v['integrity'].split('-',1);expected=base64.b64decode(value,validate=True)
        archive=fetch(v['resolved'],cache/(hashlib.sha256(v['resolved'].encode()).hexdigest()+'.tgz'),expected,algorithm)
        archives.append((name,archive))
    node_archive=fetch(node['url'],cache/Path(node['url']).name,bytes.fromhex(node['archive_sha256']))
    with tempfile.TemporaryDirectory(prefix='.assemble-',dir=out) as temp:
        stage=Path(temp);tree=stage/'nvm';tree.mkdir()
        for e in nvm['files']:
            p=fetch(e['url'],cache/('nvm-'+nvm['revision'])/e['path'],bytes.fromhex(e['sha256']))
            shutil.copyfile(p,tree/e['path']);(tree/e['path']).chmod(int(e['mode'],8))
        (tree/'alias').mkdir();(tree/'alias/default').write_text('v'+node['version']+'\n')
        version=tree/'versions/node'/('v'+node['version']);version.parent.mkdir(parents=True)
        with tarfile.open(node_archive) as tar:tar.extractall(stage/'node',filter='data')
        shutil.move(stage/'node'/Path(node['url']).name.removesuffix('.tar.xz'),version)
        if io.file_hash(version/'bin/node')!=node['node_binary_sha256']:raise ValueError('Node binary mismatch')
        modules=version/'lib/node_modules'
        for name,archive in archives:
            unpack=stage/'unpack';unpack.mkdir()
            with tarfile.open(archive) as tar:tar.extractall(unpack,filter='data')
            roots=list(unpack.iterdir())
            if len(roots)!=1 or not roots[0].is_dir():raise ValueError('Unexpected npm tar root')
            target=version/'lib'/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(roots[0],target);unpack.rmdir()
            pkg=io.read_json(target/'package.json')
            if pkg['version']!=entries[name]['version']:raise ValueError('Package version differs')
            bins=pkg.get('bin',{});bins={pkg['name']:bins} if isinstance(bins,str) else bins
            for p in bins.values():
                executable=io.contained(target,p)
                if executable.exists():executable.chmod(0o755)
        qwen=modules/'@qwen-code/qwen-code';package=io.read_json(qwen/'package.json')
        (version/'bin/qwen').symlink_to(os.path.relpath(qwen/package['bin']['qwen'],version/'bin'))
        normalize_runtime_modes(tree)
        inventory=installer.inventory(tree);io.write_json(out/'inventory.json',inventory)
        shutil.copyfile(locked/'install_bundle.py',out/'install_bundle.py')
        def norm(info):
            info.uid=info.gid=info.mtime=0;info.uname=info.gname='';info.pax_headers={};return info
        with (out/'bundle.tar.gz').open('wb') as raw,gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=6) as gz:
            with tarfile.open(fileobj=gz,mode='w',dereference=False) as tar:tar.add(tree,arcname='nvm',filter=norm)
    locks=[io.evidence_ref(locked/n,ROOT) for n in ('package.json','package-lock.json','node.lock.json','nvm.lock.json')]
    manifest={'schema_version':1,'kind':'dbt-bench-qwen-bundle-v1','platform':node['platform'],
        'qwen_version':'0.26.5','node_version':node['version'],'npm_version':node['npm_version'],
        'nvm_revision':nvm['revision'],'entries':len(inventory),'locks':locks,
        'files':{k:{'path':n,'sha256':io.file_hash(out/n)} for k,n in
            [('archive','bundle.tar.gz'),('inventory','inventory.json'),('installer','install_bundle.py')]},
        'build_method':'integrity_checked_tarballs_no_foreign_execution',
        'target_cli_smoke':'required_by_native_adapter_before_model_request'}
    if require_pin:
        pin=io.read_json(locked/'bundle.lock.json')
        if manifest['locks']!=pin['locks'] or {k:v['sha256'] for k,v in manifest['files'].items()}!=pin['files']:
            raise ValueError('Rebuilt x64 payload differs from pinned complete inventory/archive')
    io.write_json(out/'bundle.json',manifest)
    io.write_json(out/'assembly.json',{'packages':len(entries),'inventory_entries':len(inventory),
        'native_target_execution':False,'model_requests':0,'package_versions':{n:v['version'] for n,v in entries.items()}})
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,required=True)
    a=p.parse_args();build(a.output,a.cache);print(a.output/'bundle.json')
