#!/usr/bin/env python3
"""Add only the x64 bundle compatibility edit to an already patched local runtime."""
from pathlib import Path
import argparse,hashlib,importlib.util,sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from apply_runtime_patches import replace_installed_file

def apply():
    package=Path(importlib.util.find_spec('harbor').origin).parent
    meta=io.read_json(ROOT/'patches/harbor-025-qwen-platform-v1/metadata.json')
    old=io.read_json(ROOT/'release/harbor-package-files.json');new=io.read_json(ROOT/'release/harbor-amd64-package-files.json')
    for item in old:
        actual=io.file_hash(package/item['path'])
        allowed={item['sha256']}
        if item['path']==meta['path']:allowed.add(meta['after_sha256'])
        if actual not in allowed:raise ValueError('Existing runtime differs: '+item['path'])
    target=package/meta['path']
    if io.file_hash(target)!=meta['after_sha256']:
        text=target.read_text()
        if text.count(meta['before_text'])!=1:raise ValueError('Patch context differs')
        text=text.replace(meta['before_text'],meta['after_text'],1)
        if hashlib.sha256(text.encode()).hexdigest()!=meta['after_sha256']:raise ValueError('Postimage mismatch')
        replace_installed_file(target,text)
    for item in new:
        if io.file_hash(package/item['path'])!=item['sha256']:raise ValueError('Final runtime differs')
    print('x64 native Qwen installation compatibility verified; no model requests')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--platform',choices=['linux-amd64'],required=True);p.parse_args();apply()
