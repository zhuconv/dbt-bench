#!/usr/bin/env python3
"""Fail closed on Debian/Python version drift; run only during image builds."""
import argparse,hashlib,json,os,subprocess,sys,tarfile,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--arch',choices=['amd64','arm64'],required=True);a=p.parse_args()
root=Path('/opt/dbt-bench-build/locks');spec=json.loads((root/'build.json').read_text())
def run(args):subprocess.run(args,check=True)
# Snapshot repositories retain the exact binary versions. No unversioned apt installs.
for p in Path('/etc/apt/sources.list.d').glob('*'):p.unlink()
Path('/etc/apt/sources.list').write_text(''.join('deb [check-valid-until=no] https://snapshot.debian.org/archive/'+archive+'/'+date+'/ '+suite+' main\n' for date in ('20260807T000000Z','20261029T000000Z','20261104T000000Z','20261106T000000Z','20261112T000000Z') for archive,suite in [('debian','trixie'),('debian-security','trixie-security')]))
run(['apt-get','-o','Acquire::Check-Valid-Until=false','update'])
run(['apt-get','install','-y','--no-install-recommends','--allow-downgrades',*(root/'apt.txt').read_text().splitlines()])
actual=dict(line.split('\t') for line in subprocess.check_output(['dpkg-query','-W','-f=${binary:Package}\t${Version}\n'],text=True).splitlines())
actual={k.removesuffix(':'+a.arch):v for k,v in actual.items()}
expected=dict(line.split('=',1) for line in (root/'apt.txt').read_text().splitlines())
if actual!=expected:raise ValueError('Debian inventory differs; do not silently relax architecture/package versions')
if subprocess.check_output(['/usr/local/bin/python3','-c','import platform;print(platform.python_version())'],text=True).strip()!=spec['python_version']:raise ValueError('Python interpreter version differs')
run(['/usr/local/bin/python3','-m','pip','install','--no-deps','--only-binary=:all:','--require-hashes','-r',str(root/'python.txt')])
run(['/usr/local/bin/python3','-m','venv','/opt/harbor-server'])
run(['/opt/harbor-server/bin/python3','-m','pip','install','--no-deps','--only-binary=:all:','--require-hashes','-r',str(root/'server.txt')])
for exe,expected in spec['python'].items():
 code='import importlib.metadata as m,json;print(json.dumps({d.metadata["Name"]:d.version for d in m.distributions()}))'
 actual=json.loads(subprocess.check_output([exe,'-c',code],text=True))
 if actual!=expected:raise ValueError('Python inventory differs: '+exe)
 run([exe,'-m','pip','check'])
# ADE's native setup/runtime tools; official architecture-specific archives.
if spec['source']=='ade':
 n=json.loads((root/('node-'+a.arch+'.json')).read_text());dest=Path('/tmp/node.tar.xz')
 with urllib.request.urlopen(n['url'],timeout=120) as response:dest.write_bytes(response.read())
 if hashlib.sha256(dest.read_bytes()).hexdigest()!=n['archive_sha256']:raise ValueError('Node archive differs')
 run(['tar','-xJf',str(dest),'--strip-components=1','-C','/usr/local']);dest.unlink()
 y=json.loads((root/'yq.json').read_text())[a.arch]
 with urllib.request.urlopen(y['url'],timeout=120) as response:binary=response.read()
 if hashlib.sha256(binary).hexdigest()!=y['sha256']:raise ValueError('yq differs')
 Path('/usr/local/bin/yq').write_bytes(binary);Path('/usr/local/bin/yq').chmod(0o755)
 if subprocess.check_output(['node','--version'],text=True).strip()!='v22.23.4':raise ValueError('Node version')
 if subprocess.check_output(['npm','--version'],text=True).strip()!='10.9.9':raise ValueError('npm version')
Path('/opt/dbt-bench-base-receipt.json').write_text(json.dumps({'architecture':a.arch,'software':spec},sort_keys=True))
