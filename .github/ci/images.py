#!/usr/bin/env python3
"""CI helper for the image build workflow (.github/workflows/build-images.yml).

It lives outside scripts/ on purpose: files under scripts/ and src/ are part of
the measurement code identity, and CI tooling must not change that identity.

  plan       print the seed/rest build matrices for $GITHUB_OUTPUT
  fetch      download and verify one task archive and its original arm64 SIF
  summarize  merge per-image build records into build-summary.json
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.io import tree_hash  # noqa: E402

RELEASE = 'https://github.com/zhuconv/dbt-bench/releases/download/assets-v1-20261112/'
PACKAGE = 'ghcr.io/zhuconv/dbt-bench-images'
# The task Codex's image README names as the first control for a shared image.
PREFERRED = {'deb__dbt-session-attribution'}


def load(path):
    return json.loads((ROOT / path).read_text())


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()


def images(platform):
    arch = 'linux-' + platform.removeprefix('linux/')
    return [r for r in load('images/platform-images.json')['images'] if r['platform'] == arch]


def representative(entry):
    keys = sorted(entry['task_keys'])
    return next((k for k in keys if k in PREFERRED), keys[0])


def cmd_plan(a):
    wanted = {w.strip() for w in a.only.split(',') if w.strip()}
    rows = []
    for e in images(a.platform):
        sha = e['canonical_asset'].removeprefix('image-')
        if wanted and not any(w == sha or sha.startswith(w) or w in e['task_keys'] for w in wanted):
            continue
        if e['status'] == 'published' and not a.rebuild:
            continue
        rows.append({'image': sha, 'short': sha[:12], 'task': representative(e),
                     'group': e['base_group'], 'source': e['source']})
    seen, seed, rest = set(), [], []
    for r in sorted(rows, key=lambda r: (r['group'], r['task'])):
        (rest if r['group'] in seen else seed).append(r)
        seen.add(r['group'])
    # GitHub rejects an empty matrix, so callers skip a job whose list is empty.
    print('seed=' + json.dumps(seed))
    print('rest=' + json.dumps(rest))
    print(f'{len(seed)} seed + {len(rest)} rest images', file=sys.stderr)


def download(url, dest, size, digest):
    """url may be a Request. Checks the declared size while streaming and the SHA256 at the end."""
    with urllib.request.urlopen(url, timeout=300) as r, open(dest, 'xb') as f:
        total = 0
        while chunk := r.read(1 << 22):
            total += len(chunk)
            if total > size:
                raise SystemExit(f'{url}: larger than the declared {size} bytes')
            f.write(chunk)
    if total != size or sha256(dest) != digest:
        raise SystemExit(f'{getattr(url, "full_url", url)}: size or SHA256 differs from release/upload-manifest.json')


def pull_sif(tag, dest, size, digest):
    """Anonymous OCI pull of the single SIF layer.

    `apptainer pull` refuses an arm64 SIF on an amd64 host, and here the SIF is
    only read with unsquashfs, so fetch the layer blob directly.
    """
    repo = PACKAGE.removeprefix('ghcr.io/')
    with urllib.request.urlopen(f'https://ghcr.io/token?scope=repository:{repo}:pull', timeout=60) as r:
        token = json.load(r)['token']

    def request(path, accept=None):
        req = urllib.request.Request(f'https://ghcr.io/v2/{repo}/{path}')
        # Not forwarded on the redirect to the blob storage host.
        req.add_unredirected_header('Authorization', 'Bearer ' + token)
        if accept:
            req.add_header('Accept', accept)
        return req

    with urllib.request.urlopen(request(f'manifests/{tag}', 'application/vnd.oci.image.manifest.v1+json'),
                                timeout=60) as r:
        manifest = json.load(r)
    layers = [x for x in manifest.get('layers', []) if x.get('mediaType') == 'application/vnd.sylabs.sif.layer.v1.sif']
    if len(layers) != 1 or layers[0]['digest'] != 'sha256:' + digest or layers[0]['size'] != size:
        raise SystemExit(f'{tag}: registry manifest does not hold exactly the expected SIF layer')
    download(request(f'blobs/sha256:{digest}'), dest, size, digest)


def cmd_fetch(a):
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    row = next(r for r in load('catalog/measurement-tasks.json')['tasks'] if r['task_key'] == a.task)
    if row['image_asset'] != 'image-' + a.image:
        raise SystemExit(f'{a.task} is not bound to image {a.image}')
    objects = load('release/upload-manifest.json')['objects']
    task_obj = next(o for o in objects if o['kind'] == 'task-archive' and row['task_asset'] in o['asset_ids'])
    sif_obj = next(o for o in objects if o['kind'] == 'sif' and row['image_asset'] in o['asset_ids'])

    archive = out / task_obj['filename']
    download(RELEASE + task_obj['filename'], archive, task_obj['bytes'], task_obj['sha256'])
    extracted = out / 'extracted'
    extracted.mkdir()
    with tarfile.open(archive) as t:
        t.extractall(extracted, filter='tar')
    task_tree = extracted / task_obj['extract_subdir']
    if tree_hash(task_tree) != row['task_tree_sha256']:
        raise SystemExit('Task tree hash differs after extraction')

    sif = out / 'original.sif'
    pull_sif(sif_obj['tag'], sif, sif_obj['bytes'], sif_obj['sha256'])
    print(json.dumps({'task_tree': str(task_tree), 'original_sif': str(sif),
                      'task_archive_sha256': task_obj['sha256'], 'original_sif_sha256': sif_obj['sha256']}))


def cmd_summarize(a):
    records = []
    for p in sorted(Path(a.records).glob('*/build-record.json')):
        records.append(json.loads(p.read_text()))
    expected = {r['image'] for r in json.loads(a.planned)}
    got = {r['image'] for r in records}
    for missing in sorted(expected - got):
        records.append({'image': missing, 'stage_failed': 'no build record (job did not finish)'})
    ok = [r for r in records if r.get('smoke', {}).get('reference_passed') is True]
    summary = {'schema_version': 1, 'platform': a.platform, 'run_id': os.environ.get('GITHUB_RUN_ID'),
               'commit': os.environ.get('GITHUB_SHA'), 'planned': len(expected), 'passed': len(ok),
               'images': sorted(records, key=lambda r: r['image'])}
    Path(a.output).write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    lines = [f'## {a.platform} images: {len(ok)}/{len(expected)} built and passed the reference smoke', '',
             '| image | task | SIF SHA256 | smoke | failed stage |', '|---|---|---|---|---|']
    for r in summary['images']:
        lines.append('| {} | {} | {} | {} | {} |'.format(
            r['image'][:12], r.get('task', ''), (r.get('sif_sha256') or '')[:16],
            r.get('smoke', {}).get('reference_passed', ''), r.get('stage_failed', '')))
    step = os.environ.get('GITHUB_STEP_SUMMARY')
    if step:
        with open(step, 'a') as f:
            f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    x = sub.add_parser('plan')
    x.add_argument('--platform', default='linux/amd64', choices=['linux/amd64', 'linux/arm64'])
    x.add_argument('--only', default='', help='comma list of image SHA256 prefixes or task keys')
    x.add_argument('--rebuild', action='store_true', help='include images already published')
    x = sub.add_parser('fetch')
    x.add_argument('--image', required=True)
    x.add_argument('--task', required=True)
    x.add_argument('--out', required=True)
    x = sub.add_parser('summarize')
    x.add_argument('--platform', required=True)
    x.add_argument('--records', required=True)
    x.add_argument('--planned', required=True, help='JSON list of planned matrix rows')
    x.add_argument('--output', required=True)
    a = p.parse_args()
    if a.cmd == 'fetch' and not re.fullmatch('[0-9a-f]{64}', a.image):
        raise SystemExit('--image must be a full SHA256')
    {'plan': cmd_plan, 'fetch': cmd_fetch, 'summarize': cmd_summarize}[a.cmd](a)


if __name__ == '__main__':
    main()
