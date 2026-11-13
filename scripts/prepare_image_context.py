#!/usr/bin/env python3
"""Prepare only local build inputs; never build/push images or execute a task."""
import argparse,json,os,re,shlex,shutil,subprocess,sys,tarfile,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io


def prepare(key,platform,task,image,out):
    out=Path(out).resolve();task=Path(task).resolve();image=Path(image).resolve()
    if out.exists():raise ValueError('Use a new build context')
    rows=io.read_json(ROOT/'catalog/measurement-tasks.json')['tasks'];row=next(r for r in rows if r['task_key']==key)
    definition=next(r for r in io.read_json(ROOT/'images/platform-images.json')['images'] if key in r['task_keys'])
    if io.tree_hash(task)!=row['task_tree_sha256'] or io.file_hash(image)!=row['image_asset'].removeprefix('image-'):
        raise ValueError('Release task or original SIF differs')
    out.mkdir(parents=True);context=out/'task-rootfs';context.parent.mkdir(exist_ok=True)
    listing=subprocess.check_output(['apptainer','sif','list',str(image)],text=True)
    offset=re.search(r'\|(\d+)-\d+\|FS',listing.replace(' ',''))[1]
    # This allowlist excludes interpreters, system libraries, gold, tests and solutions.
    allowed=['app','workspace','scripts','opt/dbt-bench-initial','entrypoint.sh']
    subprocess.run(['unsquashfs','-no-progress','-processors','2','-no-xattrs','-o',offset,'-d',str(context),str(image),*allowed],check=True)
    # Executable payload detection protects the architecture-independent boundary.
    for p in context.rglob('*'):
        if p.is_file() and not p.is_symlink():
            with p.open('rb') as stream:magic=stream.read(4)
            if magic==b'\x7fELF':raise ValueError('Native binary in task layer: '+str(p.relative_to(context)))
    # Prefer bytes directly from the verified Release task where native setup
    # left them unchanged. Changed/generated files come from its bound snapshot.
    source_files=[]
    mappings=[('workspace','environment/dbt_project')] if row['source']=='spider2' else [('app','environment/project'),('scripts','environment/shared-scripts')]
    for destination,relative_source in mappings:
        source=task/relative_source
        if not source.is_dir():continue
        for p in source.rglob('*'):
            target=context/destination/p.relative_to(source)
            if p.is_file() and not p.is_symlink() and target.is_file() and not target.is_symlink() and io.file_hash(p)==io.file_hash(target):
                shutil.copyfile(p,target);source_files.append({'image_path':'/'+str(target.relative_to(context)),
                    'task_path':str(p.relative_to(task)),'sha256':io.file_hash(p)})
    io.write_json(out/'task-source-files.json',source_files)
    # Retain task package input in the build receipt; only initial agent-visible
    # filesystem from its qualified SIF enters the image. Hidden tests stay out.
    env=context/'opt/dbt-bench-initialize.sh';env.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/definition['native_initializer'],env)
    apptainer_env=context/'.singularity.d/env/91-dbt-bench.sh';apptainer_env.parent.mkdir(parents=True,exist_ok=True)
    apptainer_env.write_text('. /opt/dbt-bench-initialize.sh\n')
    locks=out/'locks';shutil.copytree(ROOT/'images/locks'/definition['base_group'],locks)
    group=io.read_json(ROOT/'images/locks/software-groups.json')[definition['base_group']];io.write_json(locks/'build.json',group)
    for name,src in [('node-arm64.json','runtime/qwen-code/node.lock.json'),('node-amd64.json','runtime/qwen-code/linux-x64/node.lock.json'),('yq.json','images/locks/yq.json')]:
        shutil.copyfile(ROOT/src,locks/name)
    shutil.copytree(ROOT/'images/tools',out/'tools')
    for name in ('Dockerfile.base','Dockerfile.task'):shutil.copyfile(ROOT/'images'/name,out/name)
    # One BuildKit graph avoids assuming a docker-container builder can resolve
    # an intermediate image loaded only into the host Docker daemon.
    base_text=(out/'Dockerfile.base').read_text().replace('FROM ${BASE_IMAGE}','FROM ${BASE_IMAGE} AS software',1)
    task_text=(out/'Dockerfile.task').read_text()
    task_text=task_text[task_text.index('FROM scratch AS task-data'):].replace('FROM ${SOFTWARE_IMAGE}','FROM software',1)
    (out/'Dockerfile').write_text(base_text+'\n'+task_text)
    arch=platform.removeprefix('linux/');upstream=io.read_json(ROOT/'images/upstreams.lock.json')[group['upstream']]['platforms'][arch]
    base='dbt-bench-base:'+definition['base_group']+'-'+arch;target='dbt-bench-task:'+key+'-'+arch
    commands=[['docker','buildx','build','--platform',platform,'--load','-f','Dockerfile',
        '--build-arg','BASE_IMAGE='+upstream,'--build-arg','TASK_WORKDIR='+definition['working_dir'],'-t',target,'.']]
    io.write_text(out/'build-commands.sh','#!/bin/sh\nset -eu\n'+ '\n'.join(shlex.join(c) for c in commands)+'\n')
    receipt={'task_key':key,'task_tree_sha256':row['task_tree_sha256'],'canonical_image_sha256':io.file_hash(image),
             'platform':platform,'upstream':upstream,'base_group':definition['base_group'],'files_from_task_release':len(source_files),
             'working_dir':definition['working_dir'],'source_release':'assets-v1-20261112',
             'scope':'Architecture-independent initial payload, exact pinned software recipe; no build/smoke performed'}
    io.write_json(out/'context.json',receipt);return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task',required=True);p.add_argument('--platform',choices=['linux/amd64','linux/arm64'],required=True)
    p.add_argument('--task-tree',type=Path,required=True);p.add_argument('--original-sif',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(prepare(a.task,a.platform,a.task_tree,a.original_sif,a.output),indent=2))
