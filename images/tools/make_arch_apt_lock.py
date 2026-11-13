#!/usr/bin/env python3
"""Derive apt-<arch>.txt from each software group's arm64 apt.txt.

Debian sometimes rebuilds a package on one architecture only (a binNMU, version
suffix +bN), so the same source version can carry different binary versions on
arm64 and amd64. For every pinned arm64 binary this picks the target-architecture
binary built from the same Debian source version, using the snapshot indexes that
install_base.py installs from:

  - the exact version exists for the target architecture: kept;
  - it does not, but the arm64 indexes record its source: replaced by the single
    target binary of that package built from the same source version (else error);
  - it is in no snapshot index (preinstalled by the pinned base image, e.g. from
    trixie-updates or a vendor repository): kept; install_base.py's inventory check
    still requires exactly that version.

Writes images/locks/<group>/apt-<arch>.txt and images/locks/apt-<arch>-map.json.
"""
import argparse
import collections
import json
import lzma
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Keep in sync with install_base.py.
DATES = ('20260807T000000Z', '20261029T000000Z', '20261104T000000Z', '20261106T000000Z', '20261112T000000Z')
SUITES = (('debian', 'trixie'), ('debian-security', 'trixie-security'))


def indexes(arch):
    db = collections.defaultdict(dict)
    for date in DATES:
        for archive, suite in SUITES:
            url = f'https://snapshot.debian.org/archive/{archive}/{date}/dists/{suite}/main/binary-{arch}/Packages.xz'
            with urllib.request.urlopen(url, timeout=300) as r:
                text = lzma.decompress(r.read()).decode()
            for para in text.split('\n\n'):
                f = dict(re.findall(r'^([A-Za-z-]+): (.*)$', para, re.M))
                if 'Package' in f:
                    m = re.fullmatch(r'(\S+)(?: \((\S+)\))?', f.get('Source', f['Package']))
                    db[f['Package']][f['Version']] = (m[1], m[2] or f['Version'])
    return db


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--arch', default='amd64', choices=['amd64'])
    a = p.parse_args()
    source, target = indexes('arm64'), indexes(a.arch)
    changes = []
    for group in sorted(d for d in (ROOT / 'images/locks').iterdir() if (d / 'apt.txt').is_file()):
        out = []
        for line in (group / 'apt.txt').read_text().splitlines():
            pkg, ver = line.split('=', 1)
            src = source.get(pkg, {}).get(ver)
            if ver in target.get(pkg, {}) or src is None:
                out.append(line)
                continue
            same = [v for v, s in target.get(pkg, {}).items() if s == src]
            if len(same) != 1:
                raise SystemExit(f'{group.name}: {pkg}={ver} (source {src}) has {len(same)} {a.arch} binaries')
            out.append(f'{pkg}={same[0]}')
            changes.append({'group': group.name, 'package': pkg, 'arm64_version': ver,
                            f'{a.arch}_version': same[0], 'source': src[0], 'source_version': src[1]})
        (group / f'apt-{a.arch}.txt').write_text('\n'.join(out) + '\n')
    record = {'schema_version': 1, 'arch': a.arch, 'rule': 'same Debian source version as the arm64 pin',
              'snapshot_dates': list(DATES), 'changes': changes}
    (ROOT / f'images/locks/apt-{a.arch}-map.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
