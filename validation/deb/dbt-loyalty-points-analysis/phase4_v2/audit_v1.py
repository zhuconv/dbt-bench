"""Loyalty audit revision: explicit nearest presentation, full five-model scope."""
from decimal import Decimal as D, ROUND_HALF_UP, ROUND_HALF_DOWN, ROUND_HALF_EVEN
import math
from common import ck,finish,table_check,cleaned,INTS,NUMS

def nearest(value, places):
    return {value.quantize(D(1).scaleb(-places),rounding=mode)
            for mode in (ROUND_HALF_UP,ROUND_HALF_DOWN,ROUND_HALF_EVEN)}

def permitted_rate(value, exact):
    if type(value) not in (int,float,D) or not D(str(value)).is_finite(): return False
    actual=D(str(value))
    # Two decimal percentage presentation is the independently blind/native-
    # agreeing interpretation. Greater precision or exact binary evaluation is
    # also allowed; coarse integers/one-place rounding is not inferred.
    return (actual==exact or type(value) is float and value==float(exact) or
            any(actual in nearest(exact,p) for p in range(2,19)))

def evaluate(outputs, expected, workflow=None):
    e=expected['derived'];checks={}
    for role,name,key,cols in [
        ('transactions','stg_lp_transactions','transaction_id',list(e['stages']['transactions'][0])),
        ('programs','stg_lp_programs','program_id',list(e['stages']['programs'][0])),
        ('customers','stg_lp_customers','customer_id','customer_id first_name last_name email status'.split())]:
        checks[name]=table_check(outputs.get(name,{}),cleaned(e['stages'][role]),(key,),required=cols)
    checks['int_loyalty_metrics']=table_check(outputs.get('int_loyalty_metrics',{}),e['intermediate'],('program_id',))
    target=[dict(r) for r in e['final']];got={r.get('program_id'):r for r in outputs.get('program_loyalty_summary',{}).get('rows',[])};presentation=[];errors=[]
    for r in target:
        pid=r['program_id'];ex=e['exact'][pid];actual=got.get(pid,{})
        avg=actual.get('avg_points_per_member');rate=actual.get('redemption_rate')
        avg_ok=type(avg) is int and D(avg) in nearest(ex['average'],0)
        rate_ok=permitted_rate(rate,ex['rate']) if ex['rate'] is not None else rate==0
        if avg_ok:r['avg_points_per_member']=avg
        if rate_ok:r['redemption_rate']=rate
        if not avg_ok:errors.append([pid,'avg_points_per_member'])
        if not rate_ok:errors.append([pid,'redemption_rate'])
        presentation.append(dict(program_id=pid,exact_average=str(ex['average']),actual_average=avg,
                                 exact_rate=str(ex['rate']),actual_rate=str(rate)))
    types={c:('VARCHAR',) if c in ('program_id','program_name') else INTS if c=='avg_points_per_member' else NUMS for c in target[0]}
    checks['program_loyalty_summary']=table_check(outputs.get('program_loyalty_summary',{}),target,('program_id',),
        materializations=('TABLE','BASE TABLE'),not_null=target[0],types=types)
    checks['number_presentation']=ck('fail' if errors else 'pass',
        'Integer average is nearest integer with either exact tie direction. Percentage is nearest at 2 or more decimal places, or exact finite/binary evaluation; no global tolerance.',errors=errors,facts=presentation)
    checks['workflow']=workflow or ck('pending','Actual delivered source/build evidence absent')
    return finish(checks)
