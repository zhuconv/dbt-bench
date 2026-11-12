"""Read-only collector executed by native Harbor after agent and before verifier.

Only writes the declared logs/artifacts folder. No builds, dependency changes,
gold or verifier imports. Native scoring proceeds even if collection fails.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import duckdb

TABLES = {
    'marketing': ('stg_fact_marketing_spend', 'date_key, channel_key, impressions, clicks, conversions, spend_amount, revenue_attributed'),
    'dates': ('stg_analytics__dim_date', 'date_key, full_date'),
    'sales': ('stg_analytics__fact_sales', 'customer_key, channel_key, date_key, order_id, total_amount, profit_amount'),
    'customers': ('stg_analytics__dim_customer', 'customer_key, segment_name, tier_name'),
    'channels': ('stg_analytics__dim_channel', 'channel_key, channel_name, channel_type'),
    'output': ('rpt_channel_roi_payback_monthly', '*')}
PROJECT = Path('/app/dbt_models_duckdb')
FILES = {'submission.sql': 'models/marts/marketing/rpt_channel_roi_payback_monthly.sql',
         'submission.yml': 'models/marts/marketing/rpt_channel_roi_payback_monthly.yml',
         'dbt-manifest.json': 'target/manifest.json'}


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'phase': 'after_agent_before_native_verifier',
              'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'read_only_database': True,
              'dbt_invoked': False, 'files': {}, 'tables': {}, 'errors': []}
    for name, source in FILES.items():
        path = PROJECT/source
        if path.is_file():
            payload = path.read_bytes()
            (out/name).write_bytes(payload)
            report['files'][name] = {'source': str(path), 'sha256': hashlib.sha256(payload).hexdigest()}
        else:
            report['files'][name] = {'source': str(path), 'absent': True}
    try:
        c = duckdb.connect('/app/database/retail.duckdb', read_only=True)
        for name, (table, columns) in TABLES.items():
            try:
                exists = c.execute('SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND lower(table_name)=?', ['main', table]).fetchone()[0]
                if not exists:
                    report['files'][name+'.json'] = {'absent': True, 'table': 'main.'+table}
                    report['tables'][name] = {'table': 'main.'+table, 'absent': True}
                    continue
                q = c.execute('SELECT '+columns+' FROM main.'+table)
                names = [column[0].lower() for column in q.description]
                assert len(names) == len(set(names)), 'Duplicate column names'
                types = {column[0].lower(): str(column[1]) for column in q.description}
                rows = [dict(zip(names, row)) for row in q.fetchall()]
                payload = (json.dumps(rows, default=str, sort_keys=True)+'\n').encode()
                (out/(name+'.json')).write_bytes(payload)
                report['files'][name+'.json'] = {'sha256': hashlib.sha256(payload).hexdigest()}
                report['tables'][name] = {'table': 'main.'+table, 'types': types, 'rows': len(rows)}
            except Exception as exc:
                report['errors'].append({'table': table, 'type': type(exc).__name__, 'message': str(exc)})
        c.close()
    except Exception as exc:
        report['errors'].append({'database': '/app/database/retail.duckdb', 'type': type(exc).__name__, 'message': str(exc)})
    (out/'snapshot.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'output': str(out), 'errors': len(report['errors']), 'files': len(report['files'])}))


if __name__ == '__main__':
    main()
