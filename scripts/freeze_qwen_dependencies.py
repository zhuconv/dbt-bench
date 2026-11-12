#!/usr/bin/env python3
"""Create/replay an explicit Qwen npm lock, separate from native Harbor install.

The current Harbor adapter continues to use its upstream installer. These
artifacts prepare a reproducible installation; a tested integration is required
before changing any benchmark runtime identity.
"""
from pathlib import Path
import argparse,hashlib,json,os,platform,shutil,subprocess,sys,tarfile,urllib.request
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.io import evidence_ref,file_hash,read_json,write_json

VERSION='22.23.4'
ARCHIVE=f'node-v{VERSION}-linux-arm64.tar.xz'
NODE_BINARY_SHA='7e48183361053f039294fbbc3ba586722fe756bf296d6cb8195356d10819cd16'
BASE=f'https://nodejs.org/dist/v{VERSION}/'


def download(url,path):
    if path.exists():raise FileExistsError(path)
    temporary=path.with_suffix(path.suffix+'.partial')
    with urllib.request.urlopen(url,timeout=120) as response,temporary.open('wb') as stream:
        shutil.copyfileobj(response,stream)
    temporary.replace(path)


def dependency_versions(tree):
    versions=set()
    def visit(dependencies):
        for name,node in dependencies.items():
            if node.get('version'):versions.add((name,node['version']))
            visit(node.get('dependencies',{}))
    visit(tree.get('dependencies',{}))
    return versions


def package_inventory(files,prefix=''):
    """Compare package bytes across npm hoisting layouts, with explicit omissions."""
    result={};omitted=[]
    for item in files:
        parts=(prefix+item['path']).split('/');package=None
        if '..' in parts or '' in parts:raise ValueError('Unsafe inventory path')
        while parts and parts[0]=='node_modules':
            parts.pop(0)
            if not parts:raise ValueError('Incomplete module path')
            if parts[0] in {'.bin','.package-lock.json'}:package=None;break
            package=parts.pop(0)
            if package.startswith('@'):
                if not parts:raise ValueError('Incomplete scoped module path')
                package+='/'+parts.pop(0)
        if package is None:omitted.append(item['path']);continue
        key=(package,'/'.join(parts));value=(item['sha256'],item['size'])
        if key in result and result[key]!=value:raise ValueError('Conflicting copies of the same package file')
        result[key]=value
    return result,omitted


def repair_libc_metadata(path,patch_dir):
    """Restore only exact upstream metadata under the recorded preimage hash."""
    metadata=read_json(patch_dir/'metadata.json')
    if file_hash(path)==metadata['after_sha256']:return
    if file_hash(path)!=metadata['applicable_before_sha256']:
        raise ValueError('Generated npm lock differs from the reviewed compatibility patch preimage')
    lock=read_json(path)
    for change in metadata['changes']:
        source=ROOT/change['source']['path']
        if file_hash(source)!=change['source']['sha256']:raise ValueError('Package metadata evidence changed')
        package=read_json(source);entry=lock['packages'][change['package']]
        if entry['version']!=change['version'] or entry['integrity']!=change['npm_integrity'] or 'libc' in entry:
            raise ValueError('Unexpected package-lock entry')
        if package['libc']!=change['libc']:raise ValueError('libc differs from upstream package')
        entry['libc']=package['libc']
    payload=json.dumps(lock,indent=2)+'\n'
    if hashlib.sha256(payload.encode()).hexdigest()!=metadata['after_sha256']:
        raise ValueError('Compatibility patch produced an unexpected lock')
    path.write_text(payload)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create-lock',action='store_true',help='Initial explicit version resolution; refuses to replace existing locks')
    parser.add_argument('--offline',action='store_true',help='Use pinned existing Node archive and npm cache; no registry resolution')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.create_lock and args.offline:raise ValueError('Initial resolution requires network')
    if platform.system()!='Linux' or platform.machine() not in {'aarch64','arm64'}:
        raise ValueError('This installation receipt is for Linux ARM64')
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(Path(__file__),out/'script.py')
    locked=ROOT/'runtime/qwen-code';cache=ROOT/'.cache/qwen-runtime';cache.mkdir(parents=True,exist_ok=True)
    archive=cache/ARCHIVE;node_lock=locked/'node.lock.json';npm_lock=locked/'package-lock.json'
    report={'started_at_utc':datetime.now(timezone.utc).isoformat(),'scope':'Linux ARM64 locked npm installation; native Harbor integration unchanged',
            'production_integration_complete':False,'qwen_smoke_executed':False,'steps':[],
            'script':evidence_ref(out/'script.py',ROOT),'offline_requested':args.offline}
    env={**os.environ,'CC':'/usr/bin/gcc','CXX':'/usr/bin/g++'}
    def run(name,argv,cwd=None):
        p=subprocess.run([str(a) for a in argv],cwd=cwd or ROOT,env=env,capture_output=True,text=True,timeout=900)
        path=out/(name+'.json');write_json(path,{'argv':[str(a) for a in argv],'cwd':str(cwd or ROOT),
            'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
        report['steps'].append({'name':name,'evidence':evidence_ref(path,ROOT),'returncode':p.returncode})
        print(name+': exit='+str(p.returncode),flush=True)
        if p.returncode:raise RuntimeError(name+' failed; original output retained')
        return p.stdout
    try:
        if args.create_lock:
            if node_lock.exists() or npm_lock.exists():raise FileExistsError('Use existing locks or introduce a new explicit version; do not resolve over them')
            sums=out/'SHASUMS256.txt';download(BASE+'SHASUMS256.txt',sums)
            expected=[line.split()[0] for line in sums.read_text().splitlines() if line.split()[-1]==ARCHIVE]
            if len(expected)!=1:raise ValueError('Expected one exact archive entry')
            if not archive.exists():download(BASE+ARCHIVE,archive)
            if file_hash(archive)!=expected[0]:raise ValueError('Node archive checksum mismatch')
            write_json(node_lock,{'version':VERSION,'npm_version':'10.9.9','platform':'linux-arm64',
                'url':BASE+ARCHIVE,'archive_sha256':expected[0],'node_binary_sha256':NODE_BINARY_SHA,
                'checksum_source':BASE+'SHASUMS256.txt','checksum_document_sha256':file_hash(sums),
                'checksum_scope':'Published SHA256 checked over TLS; no claim of release-signature verification',
                'native_observation':evidence_ref(ROOT/'artifacts/execution/qwen-native-setup-deb-v1/agent-logs/installation-inventory/summary.json',ROOT)})
        pin=read_json(node_lock)
        if pin['version']!=VERSION or pin['node_binary_sha256']!=NODE_BINARY_SHA:raise ValueError('Unexpected Node pin')
        if not archive.exists():
            if args.offline:raise FileNotFoundError('Pinned Node archive absent from local cache')
            download(pin['url'],archive)
        if file_hash(archive)!=pin['archive_sha256']:raise ValueError('Cached Node archive changed')
        node_dir=out/'node';node_dir.mkdir()
        with tarfile.open(archive,'r:xz') as tar:tar.extractall(node_dir,filter='data')
        prefix=node_dir/f'node-v{VERSION}-linux-arm64';node=prefix/'bin/node'
        if file_hash(node)!=NODE_BINARY_SHA:raise ValueError('Node differs from actual native adapter installation')
        env['PATH']=str(prefix/'bin')+os.pathsep+env.get('PATH','')
        npm=[node,prefix/'lib/node_modules/npm/bin/npm-cli.js']
        if run('node-version',[node,'--version']).strip()!='v'+VERSION:raise ValueError('Node version mismatch')
        if run('npm-version',npm+['--version']).strip()!='10.9.9':raise ValueError('npm version mismatch')
        npm_options=['--cache',cache/'npm','--no-audit','--no-fund']
        if args.create_lock:
            run('resolve-lock',npm+['install','--package-lock-only','--ignore-scripts',*npm_options],locked)
            repair_libc_metadata(npm_lock,ROOT/'patches/npm-10-lock-libc-metadata-v1')
        lock=read_json(npm_lock)
        if lock['lockfileVersion']!=3:raise ValueError('Expected npm lockfile v3')
        for name,package in lock['packages'].items():
            if not name:continue
            if not package.get('integrity') or not package.get('resolved','').startswith('https://registry.npmjs.org/'):
                raise ValueError('Unpinned/non-registry package: '+name)
        receipt=read_json(locked/'native-installation.json')
        report['locks']=[evidence_ref(p,ROOT) for p in [locked/'package.json',npm_lock,node_lock,locked/'native-installation.json']]
        results=[]
        # Two separate directories prove cache-backed reinstalls rather than
        # merely re-running an existing executable.
        for label,offline in [('first',args.offline),('replay',True)]:
            directory=out/label;directory.mkdir()
            for name in ['package.json','package-lock.json']:shutil.copyfile(locked/name,directory/name)
            run(label+'-ci',npm+['ci',*npm_options]+(['--offline'] if offline else []),directory)
            if file_hash(directory/'package-lock.json')!=file_hash(npm_lock):raise ValueError('npm ci changed locked resolution')
            executable=directory/'node_modules/.bin/qwen'
            if run(label+'-qwen-version',[node,executable,'--version']).strip()!='0.26.5':raise ValueError('Qwen version mismatch')
            tree=json.loads(run(label+'-dependency-tree',npm+['ls','--all','--json'],directory))
            versions=dependency_versions(tree)
            inventory=[]
            for p in sorted((directory/'node_modules').rglob('*')):
                if p.is_file():inventory.append({'path':str(p.relative_to(directory)),'sha256':file_hash(p),'size':p.stat().st_size})
            write_json(out/(label+'-inventory.json'),inventory)
            results.append((versions,inventory))
        if results[0]!=results[1]:raise ValueError('Independent installation inventories differ')
        expected={tuple(v) for v in receipt['dependency_versions']}
        report['matches_native_dependency_versions']=results[0][0]==expected
        if results[0][0]!=expected:raise ValueError('Locked installation differs from observed native dependency versions')
        actual,omitted=package_inventory(results[0][1])
        wanted={(r['package'],r['path']):(r['sha256'],r['size']) for r in receipt['package_files']}
        report.update(matches_native_package_files=actual==wanted,compared_native_package_files=len(wanted),
                      omitted_generated_inventory_paths=omitted)
        if actual!=wanted:raise ValueError('Locked package bytes differ from the portable native installation receipt')
        report.update(passed=True,installed_packages=len(results[0][0]),installed_files=len(results[0][1]),
                      independent_installations_identical=True,
                      offline_scope='npm --offline with a populated cache; not a system-wide network isolation claim')
    except Exception as exc:
        report.update(passed=False,error={'type':type(exc).__name__,'message':str(exc)})
    finally:
        report['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        write_json(out/'result.json',report)
    print(json.dumps({k:report.get(k) for k in ['passed','error','installed_packages','installed_files','production_integration_complete']}))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
