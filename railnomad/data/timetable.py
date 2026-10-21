"""Narrow, auditable correction of a duplicated midnight arrival-day offset."""
from .release import check_cancel
from itertools import groupby


MIDNIGHT_RULE = 'arrival_23xx_extra_day_v1'


def repair_midnight_arrivals(db, cancel=lambda: False):
    """Commit a trip's proposed fixes only if its complete sequence then passes.

    Only 23:xx arrivals encoded on the same GTFS day as a 00:xx departure
    qualify. Subtract exactly one day from arrival; preserve both clock times,
    every departure, service day, call and boarding restriction.
    """
    repairs=[]
    candidates=[r[0] for r in db.execute('SELECT DISTINCT trip FROM calls WHERE dep<arr')]
    db.execute('''CREATE TABLE timetable_repairs(
        trip TEXT,seq INTEGER,stop TEXT,original_arr INTEGER,original_dep INTEGER,
        arr INTEGER,dep INTEGER,rule TEXT,PRIMARY KEY(trip,seq))''')
    for trip in candidates:
        check_cancel(cancel)
        calls=list(db.execute('SELECT seq,stop,arr,dep FROM calls WHERE trip=? ORDER BY seq',(trip,)))
        proposed=[];changes=[]
        for seq,stop,arr,dep in calls:
            corrected=arr
            if (dep<arr and arr>=86400 and arr//86400==dep//86400
                    and arr%86400>=23*3600 and dep%86400<3600):
                corrected=arr-86400
                changes.append(dict(trip=trip,seq=seq,stop=stop,original_arr=arr,
                                    original_dep=dep,arr=corrected,dep=dep,rule=MIDNIGHT_RULE))
            proposed.append((corrected,dep))
        valid=all(arr<=dep and (i==0 or proposed[i-1][1]<=arr)
                  for i,(arr,dep) in enumerate(proposed))
        if not changes or not valid:
            continue
        for change in changes:
            db.execute('UPDATE calls SET arr=? WHERE trip=? AND seq=?',(change['arr'],trip,change['seq']))
            db.execute('INSERT INTO timetable_repairs VALUES(?,?,?,?,?,?,?,?)',tuple(change[k] for k in
                       ('trip','seq','stop','original_arr','original_dep','arr','dep','rule')))
        repairs.extend(changes)
    return repairs


def flag_unusable_events(db):
    """Flag only event values directly involved in a temporal contradiction."""
    db.execute('''CREATE TABLE call_time_issues(
        trip TEXT,seq INTEGER,stop TEXT,event TEXT,value INTEGER,reason TEXT,
        related_seq INTEGER,related_value INTEGER,
        PRIMARY KEY(trip,seq,event,reason,related_seq))''')
    issues=[]
    rows=db.execute('SELECT trip,seq,stop,arr,dep FROM calls ORDER BY trip,seq')
    for trip,part in groupby(rows,key=lambda r:r[0]):
        calls=list(part)
        for index,(_,seq,stop,arr,dep) in enumerate(calls):
            if dep<arr:
                # Origin arrival and terminus departure are not ridden events.
                # A dummy opposite event cannot disqualify the used endpoint.
                events=[('arr',arr,dep),('dep',dep,arr)]
                if index==0 and len(calls)>1:events=events[:1]
                elif index==len(calls)-1 and len(calls)>1:events=events[1:]
                for event,value,other in events:
                    issues.append(dict(trip=trip,seq=seq,stop=stop,event=event,value=value,
                                       reason='departure_before_arrival',related_seq=seq,related_value=other))
            if index and arr<calls[index-1][4]:
                _,old_seq,old_stop,_,old_dep=calls[index-1]
                issues.extend([
                    dict(trip=trip,seq=seq,stop=stop,event='arr',value=arr,reason='arrival_before_previous_departure',related_seq=old_seq,related_value=old_dep),
                    dict(trip=trip,seq=old_seq,stop=old_stop,event='dep',value=old_dep,reason='arrival_before_previous_departure',related_seq=seq,related_value=arr)])
    db.executemany('INSERT INTO call_time_issues VALUES(?,?,?,?,?,?,?,?)',
                   [tuple(r[k] for k in ('trip','seq','stop','event','value','reason','related_seq','related_value')) for r in issues])
    return issues
