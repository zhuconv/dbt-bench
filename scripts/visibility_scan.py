#!/usr/bin/env python3
"""Read-only inventory and declared-hidden-asset matching inside a task container.

This checks exact bytes and contiguous normalized source lines. It does not
prove general information-flow security or inspect compressed/binary semantics.
"""
import hashlib
import json
import os
from pathlib import Path
import pickletools
import stat
import sys


def source_lines(data):
    if b'\0' in data:
        return None
    try:
        lines = data.decode('utf-8').splitlines()
    except UnicodeDecodeError:
        return None
    return [hashlib.sha256(' '.join(line.split()).encode()).hexdigest()
            for line in lines if line.strip() and not line.lstrip().startswith(('#', '--'))]


def fingerprint(name, data):
    return {'source': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'normalized_lines': source_lines(data)}


def contains_lines(candidate, wanted):
    if not candidate or not wanted or len(candidate) < len(wanted):
        return False
    return any(candidate[i:i + len(wanted)] == wanted
               for i, value in enumerate(candidate[:len(candidate) - len(wanted) + 1])
               if value == wanted[0])


def inspect_structure(data, kind, assets, target):
    """Decode known dbt containers; pickle opcodes are inspected, never executed."""
    matches, mentions = [], []
    counts = {'strings': 0, 'binary_values': 0, 'opaque_values': 0, 'json_log_lines': 0}

    def inspect(value, location):
        if isinstance(value, str):
            counts['strings'] += 1
            raw = value.encode()
            if target in value:
                mentions.append({'location': location, 'sha256': hashlib.sha256(raw).hexdigest(), 'length': len(value)})
        elif isinstance(value, bytes):
            counts['binary_values'] += 1
            raw = value
        else:
            return
        normalized = source_lines(raw)
        for asset in assets:
            if hashlib.sha256(raw).hexdigest() == asset['sha256']:
                matches.append({'location': location, 'source': asset['source'], 'kind': 'exact_decoded_value'})
            if asset.get('normalized_lines') and contains_lines(normalized, asset['normalized_lines']):
                matches.append({'location': location, 'source': asset['source'], 'kind': 'normalized_decoded_source'})

    def visit(value, path='$'):
        if isinstance(value, dict):
            for key, child in value.items():
                inspect(key, path + '.<key>')
                visit(child, path + '.' + str(key))
        elif isinstance(value, (list, tuple)):
            for i, child in enumerate(value): visit(child, path + '[' + str(i) + ']')
        elif isinstance(value, (str, bytes)):
            inspect(value, path)
        elif value is not None and not isinstance(value, (int, float, bool)):
            counts['opaque_values'] += 1

    if kind == 'json':
        visit(json.loads(data))
    elif kind == 'msgpack':
        import msgpack
        visit(msgpack.unpackb(data, raw=False, strict_map_key=False))
    elif kind == 'pickle-opcodes':
        for opcode, argument, position in pickletools.genops(data):
            inspect(argument, 'opcode:' + opcode.name + '@' + str(position))
    elif kind == 'text-jsonlines':
        text = data.decode('utf-8')
        inspect(text, '$whole_text')
        for number, line in enumerate(text.splitlines(), 1):
            try: value = json.loads(line)
            except json.JSONDecodeError: continue
            counts['json_log_lines'] += 1
            visit(value, 'line:' + str(number))
    else:
        raise ValueError('Unsupported explicit artifact format: ' + kind)
    return {'counts': counts, 'matches': matches, 'target_mentions': mentions}


def scan(spec, roots, output):
    """Return observations, never turn absence of a match into a certification."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    excluded_roots = [Path(p) for p in spec.get('excluded_roots', [])] + [output]
    excluded_files = set(spec.get('excluded_files', []))
    sizes = {item['size'] for item in spec['hidden_assets']}
    maximum = spec.get('maximum_text_bytes', 2 * 1024 * 1024)
    errors, matches, symlinks = [], [], []
    counts = {'regular_files': 0, 'exact_hash_candidates': 0, 'text_examined': 0,
              'binary_or_non_utf8': 0, 'larger_than_text_limit': 0, 'special_files': 0}
    seen = set()

    def excluded(path):
        return str(path) in excluded_files or any(path == root or root in path.parents for root in excluded_roots)

    def error(exc):
        errors.append({'path': str(exc.filename), 'type': type(exc).__name__, 'message': str(exc)})

    with (output / 'files.jsonl').open('w') as inventory:
        for root in roots:
            for directory, subdirs, names in os.walk(root, followlinks=False, onerror=error):
                parent = Path(directory)
                if excluded(parent):
                    subdirs[:] = []
                    continue
                for name in list(subdirs):
                    path = parent / name
                    if excluded(path):
                        subdirs.remove(name)
                    elif path.is_symlink():
                        symlinks.append({'path': str(path), 'target': os.readlink(path)})
                        subdirs.remove(name)
                for name in sorted(names):
                    path = parent / name
                    if excluded(path) or str(path) in seen:
                        continue
                    seen.add(str(path))
                    try:
                        metadata = path.lstat()
                        if stat.S_ISLNK(metadata.st_mode):
                            symlinks.append({'path': str(path), 'target': os.readlink(path)})
                            continue
                        if not stat.S_ISREG(metadata.st_mode):
                            counts['special_files'] += 1
                            continue
                        counts['regular_files'] += 1
                        row = {'path': str(path), 'size': metadata.st_size, 'mode': stat.S_IMODE(metadata.st_mode)}
                        data, checksum = None, None
                        if metadata.st_size <= maximum:
                            data = path.read_bytes()
                            checksum = hashlib.sha256(data).hexdigest()
                        elif metadata.st_size in sizes:
                            h = hashlib.sha256()
                            with path.open('rb') as stream:
                                for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
                            checksum = h.hexdigest()
                        if checksum is not None: row['sha256'] = checksum
                        if metadata.st_size in sizes:
                            counts['exact_hash_candidates'] += 1
                            for asset in spec['hidden_assets']:
                                if checksum == asset['sha256']:
                                    matches.append({'path': str(path), 'source': asset['source'], 'kind': 'exact_bytes'})
                        if data is None:
                            row['text_status'] = 'larger_than_text_limit'
                            counts['larger_than_text_limit'] += 1
                        else:
                            lines = source_lines(data)
                            if lines is None:
                                row['text_status'] = 'binary_or_non_utf8'
                                counts['binary_or_non_utf8'] += 1
                            else:
                                row['text_status'] = 'examined'
                                counts['text_examined'] += 1
                                for asset in spec['hidden_assets']:
                                    wanted = asset.get('normalized_lines')
                                    if wanted and contains_lines(lines, wanted):
                                        matches.append({'path': str(path), 'source': asset['source'],
                                                        'kind': 'contiguous_normalized_source'})
                        inventory.write(json.dumps(row, sort_keys=True) + '\n')
                    except OSError as exc:
                        error(exc)
    result = {'roots': roots, 'excluded_roots': list(map(str, excluded_roots)),
              'excluded_files': sorted(excluded_files), 'counts': counts, 'errors': errors,
              'matches': matches, 'symlinks': symlinks, 'maximum_text_bytes': maximum,
              'scope': 'Exact byte matches for every declared hidden-file size; contiguous whole-source normalized lines in readable UTF-8 files up to the stated limit. Symlinks inventoried without following; target content is examined only when its real path is covered by the stated roots. No archive decoding, binary semantic inspection or general information-flow proof.'}
    (output / 'scan.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def inspect_artifacts(spec):
    """Inspect the declared formats and target; do not assume a benchmark name."""
    target = spec.get('target_relation', 'attribution_report')
    if not isinstance(target, str) or not target:
        raise ValueError('Expected a nonempty target relation')
    structured = []
    for item in spec.get('structured_artifacts', []):
        path = Path(item['path'])
        entry = {'path': str(path), 'kind': item['kind'], 'exists': path.is_file()}
        if path.is_file():
            try:
                data = path.read_bytes()
                entry.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                             result=inspect_structure(data, item['kind'], spec['hidden_assets'], target))
            except Exception as exc:
                entry['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        structured.append(entry)
    return structured


def main():
    spec_path, output = map(Path, sys.argv[1:3])
    spec = json.loads(spec_path.read_text())
    report = scan(spec, ['/'], output)
    structured = inspect_artifacts(spec)
    (output / 'structured.json').write_text(json.dumps(structured, indent=2, sort_keys=True) + '\n')
    facts = {'uid': os.getuid(), 'cwd': os.getcwd(),
             'declared_absent_paths': {p: not os.path.lexists(p) for p in spec['declared_absent_paths']},
             'host_paths_visible': {p: os.path.exists(p) for p in spec['host_paths']},
             'database_relations': [], 'proc_roots': {}}
    for path in ['/', '/proc/1/root', '/proc/self/root']:
        try:
            st = os.stat(path)
            facts['proc_roots'][path] = {'device': st.st_dev, 'inode': st.st_ino}
        except OSError as exc:
            facts['proc_roots'][path] = {'error': str(exc)}
    mountinfo = Path('/proc/self/mountinfo').read_text()
    (output / 'mountinfo.txt').write_text(mountinfo)
    import duckdb
    facts['duckdb_version'] = duckdb.__version__
    for item in spec['databases']:
        with duckdb.connect(item['path'], read_only=True) as con:
            for schema, table in item['absent_relations']:
                rows = con.execute('select table_schema,table_name,table_type from information_schema.tables where table_schema=? and table_name=?', [schema, table]).fetchall()
                facts['database_relations'].append({'database': item['path'], 'schema': schema, 'table': table, 'observed': rows})
    (output / 'facts.json').write_text(json.dumps(facts, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'scan_complete': not report['errors'], 'counts': report['counts'],
                      'matches': report['matches'], 'structured_files': len(structured),
                      'facts': facts, 'output': str(output)}))


if __name__ == '__main__': main()
