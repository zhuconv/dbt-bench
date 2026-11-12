"""Explicit dated scope plus all-noncancelled distinct counts; NULL-date/reconciliation conflicts remain visible."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal as D
from fractions import Fraction
COLS={'rpt_customer_cltv_forecast':('customer_id','as_of_date','current_lifetime_revenue','current_order_count','avg_order_value','days_since_last_order','forecast_3m_revenue','forecast_6m_revenue','forecast_12m_revenue')}
def day(x):return x.date() if isinstance(x,datetime) else x
def cents(x):
    scaled=Fraction(x)*100;lo=scaled.numerator//scaled.denominator;rem=scaled-lo
    return [D(lo)/100] if rem<Fraction(1,2) else [D(lo+1)/100] if rem>Fraction(1,2) else [D(lo)/100,D(lo+1)/100]
def month(d):return d.year*12+d.month-1
def derive(raw):
    rows=raw['enriched'];asof=max(day(r['ordered_at'])for r in rows if r['ordered_at']is not None);groups=defaultdict(list)
    for r in rows:
        if r['customer_id']is not None and r['is_cancelled']is not True and r['ordered_at']is not None and day(r['ordered_at'])<=asof:groups[r['customer_id']].append(r)
    expected={};monthly={};all_groups=defaultdict(list)
    for r in rows:
        if r['customer_id']is not None and r['is_cancelled']is False:all_groups[r['customer_id']].append(r)
    counts={cid:len({r['order_id']for r in rs if r['order_id']is not None})for cid,rs in all_groups.items()};undated={cid:sum((D(r['grand_total'])for r in rs if r['ordered_at']is None and r['grand_total']is not None),D(0))for cid,rs in all_groups.items()}
    for cid,rs in sorted(groups.items()):
        total=sum((D(r['grand_total'])for r in rs if r['grand_total']is not None),D(0));count=len({r['order_id']for r in rs if r['order_id']is not None});ms=defaultdict(lambda:D(0))
        for r in rs:
            if month(asof)-6<=month(day(r['ordered_at']))<month(asof):ms[month(day(r['ordered_at']))]+=D(r['grand_total'])if r['grand_total']is not None else D(0)
        avg=Fraction(sum(ms.values(),D(0)))/len(ms)if ms else Fraction(0);money=cents(total);assert len(money)==1,'Current source cents sum is expected exact'
        r={'customer_id':cid,'as_of_date':asof,'current_lifetime_revenue':money,'current_order_count':count,'avg_order_value':cents(Fraction(money[0])/max(count,1)),'days_since_last_order':(asof-max(day(r['ordered_at'])for r in rs)).days}
        for n in [3,6,12]:r[f'forecast_{n}m_revenue']=cents(Fraction(money[0])+n*avg)
        expected[cid]=r;monthly[cid]={'window_months':dict(ms),'active_month_count':len(ms),'average_numerator':avg.numerator,'average_denominator':avg.denominator}
    reconciliation=sum((D(r['grand_total'])for r in rows if r['is_cancelled']is False and r['grand_total']is not None),D(0))
    return {'customers':expected,'monthly':monthly,'source_reconciliation_total':reconciliation,'all_noncancelled_counts':counts,'undated_customer_amounts':undated,'nonnull_customer_total':sum((D(r['grand_total'])for rs in all_groups.values()for r in rs if r['grand_total']is not None),D(0)),'facts':{'all_noncancelled_customers':len(all_groups),'only_undated_customers':sorted(set(all_groups)-set(expected)),'nonnull_undated_amount':sum(undated.values(),D(0)),'null_customer_noncancelled_amount':sum((D(r['grand_total'])for r in rows if r['customer_id']is None and r['is_cancelled']is False and r['grand_total']is not None),D(0)),'raw_rows':len(rows),'customers':len(expected),'global_as_of_date':asof,'cancelled_rows':sum(r['is_cancelled']is True for r in rows),'null_cancelled_rows':sum(r['is_cancelled']is None for r in rows),'null_customer_rows':sum(r['customer_id']is None for r in rows),'null_date_rows':sum(r['ordered_at']is None for r in rows),'non_cent_source_values':sum(r['grand_total']is not None and D(r['grand_total'])!=D(r['grand_total']).quantize(D('.01'))for r in rows),'half_cent_cells':[(cid,k)for cid,r in expected.items()for k,v in r.items()if isinstance(v,list)and len(v)==2],'scope':'Global source date, explicit eligibility, exact Decimal sums and rational active-month averages; only exact final half-cent ties admit both nearest cents.'}}
