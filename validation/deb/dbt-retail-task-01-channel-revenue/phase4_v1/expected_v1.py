"""Declared join interpretation, independently derived from four public inputs.

Unmatched channel/date policies remain unresolved for full task certification.
"""
from collections import Counter,defaultdict
from decimal import Decimal
INPUTS={'dim_fact_sales':['sales_id','date_key','channel_key','order_id'],
 'stg_fact_sales':['sale_key','total_amount','profit_amount','discount_amount'],
 'dim_dim_channel':['channel_key','channel_id','channel_code','channel_name','channel_type'],
 'stg_analytics__dim_date':['date_key','full_date','month_number','year']}
KEYS=['month_start','sales_year','sales_month','channel_key','channel_id','channel_code','channel_name','channel_type']
MEASURES=['order_count','revenue','profit','discount_amount','margin_rate','avg_order_value','discount_rate']
COLUMNS=KEYS+MEASURES+['dbt_updated_at']
MONEY={'revenue','profit','discount_amount'}
RATIOS={'margin_rate','avg_order_value','discount_rate'}

def dec(value):return Decimal(str(value))
def divide(a,b):return a/b if a is not None and b not in (None,0) else None

def derive(inputs):
    maps={}
    for name,key in [('stg_fact_sales','sale_key'),('dim_dim_channel','channel_key'),('stg_analytics__dim_date','date_key')]:
        d=defaultdict(list)
        for row in inputs[name]:
            if row[key] is not None:d[str(row[key])].append(row)
        maps[name]=d
    groups={};stats=Counter()
    for fact in inputs['dim_fact_sales']:
        stats['fact_rows']+=1
        values=maps['stg_fact_sales'].get(str(fact['sales_id']),[]) if fact['sales_id'] is not None else []
        dates=maps['stg_analytics__dim_date'].get(str(fact['date_key']),[]) if fact['date_key'] is not None else []
        channels=maps['dim_dim_channel'].get(str(fact['channel_key']),[]) if fact['channel_key'] is not None else []
        if not values:stats['unmatched_value_rows']+=1
        if not dates:stats['unmatched_date_rows']+=1
        if not channels:stats['unmatched_channel_rows']+=1
        for value in values:
            for date in dates:
                for channel in channels or [dict.fromkeys(INPUTS['dim_dim_channel'])]:
                    key=(str(date['full_date'])[:7]+'-01',date['year'],date['month_number'],str(fact['channel_key']),*(channel[c] for c in INPUTS['dim_dim_channel'][1:]))
                    group=groups.setdefault(key,{'orders':set(),'revenue':[],'profit':[],'discount_amount':[]})
                    if fact['order_id'] is not None:group['orders'].add(fact['order_id'])
                    for dst,src in [('revenue','total_amount'),('profit','profit_amount'),('discount_amount','discount_amount')]:
                        if value[src] is not None:group[dst].append(dec(value[src]))
                    stats['joined_rows']+=1
    rows=[]
    for key,g in groups.items():
        sums={c:sum(g[c],Decimal(0)) if g[c] else None for c in MONEY}
        count=len(g['orders']);revenue=sums['revenue'];profit=sums['profit'];discount=sums['discount_amount']
        rates={'margin_rate':divide(profit,revenue),'avg_order_value':divide(revenue,Decimal(count)),
               'discount_rate':divide(discount,revenue+discount if revenue is not None and discount is not None else None)}
        rows.append(dict(zip(KEYS,key))|{'order_count':count}|{c:str(v) if v is not None else None for c,v in (sums|rates).items()})
    rows.sort(key=lambda r:(r['month_start'],r['channel_key'] or ''))
    return rows,dict(stats)

def from_captured(raw):
 return derive({"dim_fact_sales":raw["fact_keys"],"stg_fact_sales":raw["fact_values"],"dim_dim_channel":raw["channels"],"stg_analytics__dim_date":raw["dates"]})
