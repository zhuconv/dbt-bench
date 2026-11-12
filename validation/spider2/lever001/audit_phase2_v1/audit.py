"""Lever posting audit: original raw facts -> independent Python expectations.

Never imports a native checker, gold, model SQL or a complete reference solution.
This entrypoint scores the declared table only. Workflow is a separate trusted
execution check; an output-table pass does not authenticate dbt execution.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re

from audit_tables import CaptureError, CapturedTable, TablePolicy, audit_table, decode, load_observer_table
from dbt_bench.comparison import Column, TableContract

TASK = 'spider2__lever001'
DATABASE = '/workspace/lever.duckdb'
RELATION = 'lever__posting_enhanced'
RAW_TABLES = {'posting', 'application', 'interview', 'posting_interview', 'requisition_posting', 'posting_tag', 'user'}
ATTRIBUTES = {
    'posting_id': 'id', 'job_commitment': 'categories_commitment',
    'job_department': 'categories_department', 'job_level': 'categories_level',
    'job_location': 'categories_location', 'job_team': 'categories_team',
    'content_description': 'content_description', 'created_at': 'created_at',
    'creator_user_id': 'creator_id', 'owner_user_id': 'owner_id',
    'requisition_code': 'requisition_code', 'current_state': 'state',
    'job_title': 'text', 'updated_at': 'updated_at',
}
COUNTS = ('count_referred_applications', 'count_posting_applications',
          'count_manual_user_applications', 'count_opportunities',
          'count_open_opportunities', 'count_interviews', 'count_interviewees',
          'count_requisitions')
TIMESTAMPS = ('created_at', 'updated_at', 'first_app_sent_at')
REQUIRED = (*ATTRIBUTES, 'first_app_sent_at', *COUNTS, 'has_requisition',
            'tags', 'posting_hiring_manager_name')
NULL_STORAGE_UNSPECIFIED = ('job_department', 'job_level', 'requisition_code')
NUMERIC_SQL = ('TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT', 'UTINYINT',
               'USMALLINT', 'UINTEGER', 'UBIGINT', 'UHUGEINT', 'FLOAT', 'DOUBLE', 'REAL')
TIMESTAMP_SQL = ('TIMESTAMP', 'TIMESTAMP_S', 'TIMESTAMP_MS', 'TIMESTAMP_NS',
                 'TIMESTAMP WITH TIME ZONE', 'TIMESTAMPTZ')


def ref(path):
    path = Path(path)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def read_hashed(reference, root):
    path = Path(root) / reference['path']
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != reference['sha256']:
        raise CaptureError('Evidence hash mismatch: ' + str(path))
    return path, json.loads(payload)


def load_raw(reference, root):
    path, capture = read_hashed(reference, root)
    if (capture.get('task_key') != TASK or capture.get('database') != DATABASE or
            capture.get('stage') != 'initial_raw_input' or
            capture.get('read_only') is not True or capture.get('input_unchanged') is not True):
        raise CaptureError('Unrecognized raw-input capture scope')
    raw = {}
    for table in capture['tables']:
        name = table['relation'].removeprefix('main.')
        if name not in RAW_TABLES or name in raw:
            raise CaptureError('Undeclared/duplicate raw relation')
        columns = table['columns']
        if len(columns) != len(set(columns)):
            raise CaptureError('Duplicate raw column')
        filename = table['file']
        if Path(filename).name != filename:
            raise CaptureError('Raw data filename must be local')
        data = (path.parent / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != table['sha256']:
            raise CaptureError('Raw row hash mismatch')
        rows = []
        for line in data.splitlines():
            values = json.loads(line)
            if len(values) != len(columns):
                raise CaptureError('Raw row width mismatch')
            rows.append(dict(zip(columns, map(decode, values))))
        if len(rows) != table['row_count']:
            raise CaptureError('Raw row count mismatch')
        raw[name] = rows
    if set(raw) != RAW_TABLES:
        raise CaptureError('Missing declared raw source')
    return raw, capture


def active(row):
    value = row.get('_fivetran_deleted')
    if value is not None and type(value) is not bool:
        raise CaptureError('Raw deletion flag is not BOOLEAN/NULL')
    return value is not True


def timestamp(value):
    if value is None or type(value) is datetime:
        return value
    if type(value) is str:
        return datetime.fromisoformat(value)
    raise CaptureError('Raw timestamp has an unsupported representation')


def unique(rows, name):
    result = {}
    for row in rows:
        key = row['id']
        if key is None or key in result:
            raise CaptureError('Raw ' + name + ' identity is not unique and non-null')
        result[key] = row
    return result


def derive_expected(raw):
    """Second implementation: dictionaries/sets, not model SQL evaluation."""
    postings = unique([r for r in raw['posting'] if active(r)], 'posting')
    users = unique([r for r in raw['user'] if active(r)], 'user')
    interviews = unique(raw['interview'], 'interview')
    unique(raw['application'], 'application')
    applications = defaultdict(list)
    for row in raw['application']:
        applications[row['posting_id']].append(row)
    bridge = defaultdict(list)
    for row in raw['posting_interview']:
        if row['interview_id'] in interviews:
            bridge[row['posting_id']].append(interviews[row['interview_id']])
    requisitions = defaultdict(list)
    for row in raw['requisition_posting']:
        if active(row):
            requisitions[row['posting_id']].append(row['requisition_id'])
    tags = defaultdict(list)
    for row in raw['posting_tag']:
        if active(row) and row['tag'] is not None:
            tags[row['posting_id']].append(row['tag'])
    expected, ambiguity = [], []
    joined = Counter()
    for key, posting in postings.items():
        row = {column: posting[source] for column, source in ATTRIBUTES.items()}
        for col in ('created_at', 'updated_at'):
            row[col] = timestamp(row[col])
        apps = applications[key]
        times = [timestamp(a['created_at']) for a in apps if a['created_at'] is not None]
        row['first_app_sent_at'] = min(times) if times else None
        row['count_referred_applications'] = sum(a['type'] == 'referral' for a in apps)
        row['count_posting_applications'] = sum(a['type'] in ('posting', 'agency') for a in apps)
        row['count_manual_user_applications'] = sum(a['type'] == 'user' for a in apps)
        row['count_opportunities'] = len({a['opportunity_id'] for a in apps if a['opportunity_id'] is not None})
        row['count_open_opportunities'] = len({a['opportunity_id'] for a in apps if a['opportunity_id'] is not None and a['archived_at'] is None})
        ints = bridge[key]
        row['count_interviews'] = len({i['id'] for i in ints})
        row['count_interviewees'] = len({i['opportunity_id'] for i in ints if i['opportunity_id'] is not None})
        req = requisitions[key]
        if None in req or len(req) != len(set(req)):
            ambiguity.append({'posting_id': key, 'field': 'count_requisitions', 'reason': 'Null/duplicate raw requisition association needs an explicit input policy.'})
        row['count_requisitions'] = len({r for r in req if r is not None})
        row['has_requisition'] = bool(row['count_requisitions'])
        these_tags = tags[key]
        if any(type(t) is not str or ',' in t or t != t.strip() for t in these_tags):
            ambiguity.append({'posting_id': key, 'field': 'tags', 'reason': 'Comma/edge-space raw tag cannot be unambiguously parsed under the stated comma-separated interface.'})
        if len(these_tags) != len(set(these_tags)):
            ambiguity.append({'posting_id': key, 'field': 'tags', 'reason': 'Raw posting/tag key violates documented uniqueness.'})
        row['tags'] = ', '.join(sorted(these_tags)) if these_tags else None
        # Source documentation says manager at application time. It does not
        # resolve changes/ties, so never import SQL row_number tie behavior.
        managers = {users[a['posting_hiring_manager_id']]['name'] if a['posting_hiring_manager_id'] in users else None for a in apps}
        if len(managers) > 1:
            ambiguity.append({'posting_id': key, 'field': 'posting_hiring_manager_name', 'reason': 'Several application-time manager names; a unique current-manager selection rule is not stated.'})
        row['posting_hiring_manager_name'] = next(iter(managers)) if len(managers) == 1 else None
        if any(i.get('canceled_at') is not None for i in ints):
            ambiguity.append({'posting_id': key, 'field': 'count_interviews', 'reason': 'Conducted versus canceled interview handling is not explicit in report documentation.'})
        joined.update(application_rows=len(apps), interview_bridge_rows=len(ints), requisition_rows=len(req), tag_rows=len(these_tags), resolved_manager_names=sum(m is not None for m in managers))
        expected.append(row)
    return expected, {'raw_rows': {k: len(v) for k, v in raw.items()}, 'active_postings': len(postings), 'joined_source_rows': dict(joined), 'ambiguities': ambiguity, 'positive_join_evidence': any(joined.values()), 'derivation': 'Original raw fields, deletion predicates, explicit source-type categories, Python minimum timestamps and distinct identity sets; no solved SQL, gold or native checker used.'}


def policy_for(candidate, expected):
    columns, allowed = [], {}
    for name in REQUIRED:
        if name in COUNTS:
            kind, types = 'numeric', NUMERIC_SQL
            observed = candidate.physical_sql_types.get(name, '')
            if re.fullmatch(r'DECIMAL\([1-9][0-9]*,[0-9]+\)', observed):
                types += (observed,)
        elif name == 'has_requisition':
            kind, types = 'boolean', ('BOOLEAN',)
        elif name in TIMESTAMPS:
            kind, types = 'datetime', TIMESTAMP_SQL
        elif name == 'requisition_code':
            kind, types = 'exact', ('VARCHAR', 'VARCHAR[]')
        else:
            kind, types = 'text', ('VARCHAR',)
        # The task's null-only raw fields physically use INTEGER. Their
        # semantic text/array storage is undefined until a non-null value exists.
        # Whitelist alternative NULL storage only after checking every value.
        if name in NULL_STORAGE_UNSPECIFIED and all(r[name] is None for r in expected) and all(r.get(name) is None for r in candidate.rows):
            observed = candidate.physical_sql_types.get(name)
            if observed is not None:
                types = tuple(set(types + (observed,)))
        columns.append(Column(name, kind))
        allowed[name] = types
    return TablePolicy(TableContract(tuple(columns), keys=('posting_id',), ordered=False, allow_extra_columns=True), allowed, not_null=('posting_id', *COUNTS, 'has_requisition'))


def normalized_tags(candidate):
    rows = []
    for source in candidate.rows:
        row = dict(source)
        if type(row.get('tags')) is str:
            # Tags are an unordered comma-separated collection, not an SQL
            # aggregation-order contract. Multiplicity is retained by sorting.
            row['tags'] = ', '.join(sorted(t.strip() for t in row['tags'].split(',')))
        rows.append(row)
    return replace(candidate, rows=rows)


def audit_candidate(candidate, raw):
    expected, derivation = derive_expected(raw)
    if derivation['ambiguities']:
        return {'status': 'unknown', 'reward': None, 'reason': 'Executed raw investigation found an undeclared semantic boundary.', 'derivation': derivation}
    candidate = normalized_tags(candidate)
    result = audit_table(candidate, policy_for(candidate, expected), expected_rows=expected)
    result['derivation'] = derivation
    if result['status'] == 'scored':
        finite_integral = all(type(row.get(n)) in (int, float, Decimal) and math.isfinite(row[n]) and row[n] >= 0 and row[n] == int(row[n]) for row in candidate.rows for n in COUNTS)
        result['checks']['finite_nonnegative_integral_counts'] = finite_integral
        result['reward'] = float(all(result['checks'].values()))
    result['expected_rows'] = expected
    result['scope'] = 'Declared 26-column posting table and independent raw expectations only; workflow is a separate trusted execution check. Original all-zero joins are not positive-join evidence.'
    return result


def encode(value):
    if isinstance(value, datetime):
        return {'$type': 'datetime', 'value': value.isoformat()}
    if isinstance(value, Decimal):
        return {'$type': 'decimal', 'value': str(value)}
    raise TypeError(type(value).__name__)


def replay(spec, root):
    try:
        raw, capture = load_raw(spec['raw_snapshot'], root)
        candidate = load_observer_table(spec['candidate_snapshot'], task_key=TASK, database=DATABASE, relation=RELATION, stage=spec['candidate_stage'], root=Path(root))
        table = audit_candidate(candidate, raw)
        return {'schema_version': 1, 'task_key': TASK, 'table_audit': table, 'raw_snapshot': spec['raw_snapshot'], 'raw_input_sha256': capture['input_sha256'], 'candidate_snapshot': spec['candidate_snapshot'], 'workflow': {'status': 'unknown', 'reward': None, 'reason': 'No independent trusted workflow receipt is evaluated by this table component. Matching output does not prove dbt was executed or final source files were present.'}, 'model_requests': 0}
    except (CaptureError, OSError, ValueError, KeyError, TypeError) as exc:
        return {'schema_version': 1, 'task_key': TASK, 'table_audit': {'status': 'unknown', 'reward': None, 'reason': str(exc)}, 'workflow': {'status': 'unknown', 'reward': None}, 'model_requests': 0}


def main():
    p = argparse.ArgumentParser();p.add_argument('--spec', required=True);p.add_argument('--output', required=True);p.add_argument('--root', default='.');args = p.parse_args()
    spec = json.loads(Path(args.spec).read_text());result = replay(spec, args.root)
    result['implementation'] = ref(__file__)
    Path(args.output).write_text(json.dumps(result, indent=2, default=encode)+'\n')
    print(json.dumps({'task_key': TASK, 'table_status': result['table_audit']['status'], 'table_reward': result['table_audit']['reward'], 'workflow': 'unknown'}))

if __name__ == '__main__':
    main()
