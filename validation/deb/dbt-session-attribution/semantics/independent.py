"""Independent fixed-input attribution; no native SQL, Pandas or grader imports."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_UP
import json

SESSION_COLUMNS=('session_id','customer_id','session_start','referrer','utm_source','utm_medium',
                 'device_type','duration_seconds','page_views','is_converted','order_id')
SALES_COLUMNS=('order_id','total_amount')


def encoded(value):
    return json.dumps(value,default=str,sort_keys=True,separators=(',',':'))


def canonical(data):
    return {key:sorted(value,key=encoded) for key,value in data.items()}


def channel(s):
    medium=(s['utm_medium'] or '').lower();referrer=(s['referrer'] or '').lower()
    if medium in ('cpc','ppc'):return 'Paid Search'
    if medium=='paid_social' or ('facebook' in referrer and s['utm_source'] not in (None,'')):return 'Paid Social'
    if any(x in referrer for x in ('google','bing','yahoo')):return 'Organic Search'
    if any(x in referrer for x in ('facebook','twitter','linkedin','instagram')):return 'Organic Social'
    if medium=='email' or 'mail' in referrer:return 'Email'
    return 'Direct' if not referrer else 'Referral'


def calculate(data):
    sessions=data['sessions'];sales=data['sales'];coverage=Counter();unresolved=[]
    revenues=defaultdict(Decimal);quality=defaultdict(list);totals=defaultdict(lambda:[Decimal(0),Decimal(0)])
    for row in sales:
        if row['order_id'] is not None and row['total_amount'] is not None:
            revenues[row['order_id']]+=Decimal(str(row['total_amount']))
    stamps={s['session_id']:datetime.fromisoformat(str(s['session_start'])) for s in sessions}
    assert len(stamps)==len(sessions),'Session IDs must be unique for this reviewed scope'
    for s in sessions:
        if s['duration_seconds'] is None or s['page_views'] is None:
            coverage['null_quality']+=1;continue
        if s['duration_seconds']<5 or s['page_views']<2:coverage['low_quality']+=1;continue
        if (s['device_type'] or '').lower()=='bot':coverage['bot']+=1;continue
        if s['device_type'] is None:unresolved.append('null_device:'+s['session_id'])
        if s['customer_id'] is not None:quality[s['customer_id']].append(s)
    conversions=[s for s in sessions if s['is_converted'] is True and s['order_id'] in revenues]
    counts=Counter(s['order_id'] for s in conversions)
    unresolved += ['repeated_converting_order:'+str(k) for k,v in counts.items() if v>1]
    paths=[]
    with localcontext() as context:
        context.prec=50
        for conv in conversions:
            coverage['matching_conversions']+=1;stamp=stamps[conv['session_id']]
            touches=sorted((s for s in quality[conv['customer_id']]
                            if stamp-timedelta(days=14)<=stamps[s['session_id']]<stamp),key=lambda s:stamps[s['session_id']])
            n=len(touches)
            if n==0:coverage['zero_touch']+=1;continue
            coverage['single_touch' if n==1 else 'two_touch' if n==2 else 'multi_touch']+=1
            if len({stamps[s['session_id']] for s in touches})<n:unresolved.append('tied_touch:'+conv['session_id'])
            labels=[channel(s) for s in touches];bonus=len(set(labels))>=3
            coverage['bonus_paths']+=int(bonus)
            coverage['exact_lower_boundary']+=sum(stamps[s['session_id']]==stamp-timedelta(days=14) for s in touches)
            weights=[]
            for i,s in enumerate(touches):
                delta=stamp-stamps[s['session_id']]
                seconds=Decimal(delta.days*86400+delta.seconds)+Decimal(delta.microseconds)/1000000
                coverage['fractional_day_touches']+=int(seconds%86400!=0)
                position=Decimal(1) if n==1 else Decimal('1.5') if i==0 else Decimal('1.3') if i==n-1 else Decimal(1)
                weights.append(position*(-seconds/Decimal(7*86400)).exp())
            revenue=revenues[conv['order_id']]*(Decimal('1.1') if bonus else Decimal(1))
            credits=[w/sum(weights) for w in weights]
            for label,credit in zip(labels,credits):
                totals[label][0]+=credit;totals[label][1]+=credit*revenue
            paths.append({'conversion_session':conv['session_id'],'order_id':conv['order_id'],
                          'touch_ids':[s['session_id'] for s in touches],'channels':labels,'credits':list(map(str,credits)),
                          'source_revenue':str(revenues[conv['order_id']]),'adjusted_revenue':str(revenue),'bonus':bonus})
        expected=[{'channel':label,'total_conversions':str(v[0].quantize(Decimal('.01'),rounding=ROUND_HALF_UP)),
                   'total_revenue':str(v[1].quantize(Decimal('.01'),rounding=ROUND_HALF_UP))} for label,v in sorted(totals.items())]
    return {'expected':expected,'paths':paths,'coverage':dict(coverage),'unresolved':unresolved,
            'policy':'Exact IDs; Decimal 50-digit exponential and final half-up rounding. Null device, tied touches and repeated converting orders are outside this certified fixture scope.'}


def compare(expected,actual):
    required={'channel','total_conversions','total_revenue'}
    missing_columns=sorted(required-set(actual['columns']))
    rows=actual['rows'];counts=Counter(r.get('channel') for r in rows);exp={r['channel']:r for r in expected}
    duplicates={str(k):v for k,v in counts.items() if v!=1};mismatches=[]
    for row in rows:
        label=row.get('channel')
        if label not in exp:continue
        for name in ['total_conversions','total_revenue']:
            if name not in row:continue
            val=row[name]
            if val is None or not Decimal(str(val)).is_finite() or abs(Decimal(str(val))-Decimal(exp[label][name]))>Decimal('0.000000001'):
                mismatches.append({'channel':label,'column':name,'expected':exp[label][name],'actual':val})
    missing=sorted(set(exp)-set(counts));extra=sorted(set(counts)-set(exp),key=str)
    return {'equal':not any((missing_columns,duplicates,missing,extra,mismatches)),
            'missing_columns':missing_columns,'duplicate_keys':duplicates,'missing_keys':missing,'extra_keys':extra,'mismatches':mismatches}
