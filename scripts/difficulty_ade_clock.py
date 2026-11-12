"""Conservative joins of real SQL clocks, never dates invented from new runs."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SQL = "SELECT CURRENT_DATE, CURRENT_TIMESTAMP, current_setting('TimeZone')"


def stamp(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def join_clocks(original, before, after, native, *, original_capture_sha256,
                verified_session_timezone):
    """Require original and rebuild SQL dates plus an independently proven zone.

    `verified_session_timezone` is not derived by this function. A caller must
    bind genuine dbt session evidence; source or clock probes alone are not it.
    """
    missing={'status':'unknown','same_sql_date':False}
    if original is None:
        return missing|{'reason':'Original submission has no actual SQL clock capture.'}
    if not verified_session_timezone:
        return missing|{'reason':'Actual dbt session timezone is not independently established.'}
    try:
        if original['capture_sha256']!=original_capture_sha256:
            raise ValueError('Original clock is bound to a different submission capture')
        for item in (original,before,after):
            if item.get('error') or not item['input_unchanged'] or item['sql']!=SQL:
                raise ValueError('SQL clock probe failed or changed its database')
            if item['database']!='/app/f1.duckdb' or item['timezone']!=verified_session_timezone:
                raise ValueError('SQL clock database or timezone differs')
        dates={item['current_date'] for item in (original,before,after)}
        if len(dates)!=1:raise ValueError('Original and rebuild SQL dates differ')
        zone=ZoneInfo(verified_session_timezone)
        day=original['current_date']
        start,end=map(stamp,(native['started_at'],native['finished_at']))
        if stamp(original['finished_at_utc'])>start or start>end:
            raise ValueError('Clock did not precede the original native execution')
        if any(moment.astimezone(zone).date().isoformat()!=day for moment in (start,end)):
            raise ValueError('Original native execution crosses the verified SQL date')
        return {'status':'joined','same_sql_date':True,'date':day,
                'timezone':verified_session_timezone,
                'scope':'Same original/reconstruction SQL day under separately evidenced dbt session timezone.'}
    except (KeyError,ValueError,TypeError) as exc:
        return missing|{'reason':str(exc)}
