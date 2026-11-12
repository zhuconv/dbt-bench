"""Read the declared public input in a pristine validation environment."""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import sys

import duckdb


def main():
    out = Path('/logs/agent/orders-filter-input')
    out.mkdir(exist_ok=False)
    with duckdb.connect('/app/database/retail.duckdb',read_only=True) as conn:
        query = conn.execute('SELECT * FROM main.orders ORDER BY order_id')
        columns = [{'name':d[0].lower(),'type':str(d[1])} for d in query.description]
        rows = [dict(zip([c['name'] for c in columns],row)) for row in query.fetchall()]
    payload = {'columns':columns,'rows':rows}
    path = out / 'orders.json'
    path.write_text(json.dumps(payload,sort_keys=True,default=str,indent=2)+'\n')
    profile = {'input':'main.orders','rows':len(rows),'columns':columns,
        'unique_order_ids':len({r['order_id'] for r in rows}),
        'flags':{c:dict(Counter(str(r[c]) for r in rows)) for c in ['test_order_flag','sample_order_flag','internal_order_flag']},
        'status':dict(Counter(str(r['status']) for r in rows)),
        'nulls':{c['name']:sum(r[c['name']] is None for r in rows) for c in columns},
        'python':sys.executable,'duckdb':duckdb.__version__,
        'orders_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'scope':'Read-only pristine public input; no solution or verifier uploaded'}
    (out / 'profile.json').write_text(json.dumps(profile,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:profile[k] for k in ['rows','unique_order_ids','flags','orders_sha256']}))


if __name__ == '__main__':main()
