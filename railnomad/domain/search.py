"""Exact relative-time enumeration, intersecting full feasible start-date sets.

No beam, top-K, time buckets, city whitelist or recommendation pruning.
Partitions are first-boarding station IDs; restart repeats only the unfinished
partition, with content IDs deduplicating output already committed there.
"""
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
import time
from bisect import bisect_left, bisect_right
from uuid import uuid4

from ..storage import encode, now
from ..shared import SharedWriter
from ..payloads import pack,unpack
from ..evidence import evaluate,index_evidence
from .. import __version__
from ..data.release import Cancelled, check_cancel
from .model import Request, daylight, daylight_limit_minutes, in_wrapped_window, nights_for, short_window, window_relaxation, uncovered_nights, RULES_VERSION, connection_gap, arrival_allowed, departure_allowed, train_night_for_leg, transport_category, today_china,comfort_for_leg,daylight_totals_allowed


def available_departure_dates(snapshot, earliest=None):
    """Actual boarding calendar days, including GTFS service times beyond 24h."""
    earliest = earliest or today_china()
    with sqlite3.connect(snapshot['path']) as db:
        offsets = defaultdict(set)
        flagged=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='call_time_issues'").fetchone())
        boarding=" AND NOT EXISTS(SELECT 1 FROM call_time_issues f WHERE f.trip=c.trip AND f.seq=c.seq AND f.event='dep')" if flagged else ''
        alighting=" AND NOT EXISTS(SELECT 1 FROM call_time_issues f WHERE f.trip=n.trip AND f.seq=n.seq AND f.event='arr')" if flagged else ''
        for service, offset in db.execute('SELECT DISTINCT t.service,CAST(c.dep/86400 AS INTEGER) FROM calls c JOIN trips t ON t.id=c.trip WHERE c.pickup!=1'+boarding+' AND EXISTS(SELECT 1 FROM calls n WHERE n.trip=c.trip AND n.seq>c.seq AND n.arr>c.dep'+alighting+')'):
            offsets[service].add(offset)
        days = {date.fromisoformat(day).toordinal()+offset for service,day in db.execute('SELECT service,day FROM days') for offset in offsets[service]}
    return [date.fromordinal(d).isoformat() for d in sorted(days) if d >= earliest.toordinal()]


def new_run(store, snapshot, request):
    request.validate()
    from ..resources import validate_request,SearchGuard
    validate_request(request)
    SearchGuard(store,request).check(force=True)
    if request.departure_date and request.departure_date not in available_departure_dates(store.snapshot(snapshot)):
        raise ValueError('所选出发日期不是当前快照覆盖的今天或以后登车日')
    identity = uuid4().hex
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT value FROM settings WHERE key=?', ('city_mapping:' + snapshot,)).fetchone()
        from ..data.mapping import mapping_sources
        source_meta=db.execute('SELECT value FROM settings WHERE key=?',('city_mapping_provenance:'+snapshot,)).fetchone()
        checkpoint = {'mappings': json.loads(row[0]) if row else {},
                      'city_mapping_provenance': json.loads(source_meta[0]) if source_meta else {},
                      'city_mapping_sources': mapping_sources(db,snapshot),
                      'evidence': [dict(json.loads(r['payload']),evidence_id=r['id']) for r in db.execute("SELECT id,payload FROM evidence WHERE snapshot=? AND kind IN ('fare','sleeper','track') ORDER BY created,id", (snapshot,))]}
        db.execute('INSERT INTO runs(id,snapshot,request,status,checkpoint,created) VALUES(?,?,?,?,?,?)',
                   (identity, snapshot, encode(request.to_dict()), 'queued', encode(checkpoint), now()))
    return identity


class Network:
    def __init__(self, snapshot, mappings=None,check=lambda:None):
        self.resource_check=check
        self.db = sqlite3.connect(snapshot['path'])
        self.db.row_factory = sqlite3.Row
        self.stops = {r['id']: dict(r) for r in self.db.execute('SELECT * FROM stops')}
        for identity, city in (mappings or {}).items():
            if identity in self.stops:
                self.stops[identity].update(city=city, city_source='manual_mapping')
        self.calendars = defaultdict(set)
        for row in self.db.execute('SELECT service,day FROM days'):
            self.calendars[row['service']].add(date.fromisoformat(row['day']).toordinal())
        all_days = set().union(*self.calendars.values())
        self.last_day = max(all_days)
        self.first_day = min(all_days)
        self.maximum_offset = self.db.execute('SELECT MAX(arr) FROM calls').fetchone()[0] or 0
        self.by_stop = defaultdict(set)
        self.calls = defaultdict(list)
        time_issues=defaultdict(list)
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='call_time_issues'").fetchone():
            for row in self.db.execute('SELECT * FROM call_time_issues'):
                issue=dict(row);issue['stop_name']=self.stops[row['stop']]['name']
                time_issues[row['trip'],row['seq'],row['event']].append(issue)
        for row in self.db.execute('SELECT * FROM calls ORDER BY trip,seq'):
            call = dict(row)
            call['arrival_time_issues']=time_issues.get((call['trip'],call['seq'],'arr'),())
            call['departure_time_issues']=time_issues.get((call['trip'],call['seq'],'dep'),())
            self.calls[call['trip']].append(call)
            if call['pickup'] != 1 and not call['departure_time_issues']:
                self.by_stop[call['stop']].add(call['trip'])
        self.trips = {r['id']: dict(r) for r in self.db.execute('SELECT t.*,r.type,r.name route_name FROM trips t JOIN routes r ON r.id=t.route')}
        self.join_boundaries = {}
        self.request = None
        self.destinations = set()
        self.prepared = {}
        self.dated_options = None
        self.shifted_calendars = {}
        self.metrics = Counter()
        self.leg_cache = {}
        try:self.build_through_services()
        except Exception:
            self.db.close();raise

    def configure(self, request, destinations, origins=()):
        self.request, self.destinations = request, destinations
        origin_keys={self.stops[sid]['city'] or '@'+sid for sid in origins}
        destination_keys={self.stops[sid]['city'] or '@'+sid for sid in destinations}
        self.origin_key=next(iter(origin_keys)) if len(origin_keys)==1 else None
        self.destination_key=next(iter(destination_keys)) if len(destination_keys)==1 else None
        self.segments.cache_clear()

    def segment_modes(self, trip, first, last):
        """Reject invariant constraints before allocating a full segment."""
        request = self.request
        duration = last['arr']-first['dep']
        clock = first['dep'] % 86400
        short = request.allow_short_hsr and (trip['name'].startswith(('G','D','C')) or trip['type']==101) and duration <= request.short_hsr_max*60 and short_window(clock,clock+duration,request)
        sleeper = duration >= request.min_sleeper_minutes*60
        if not short and not sleeper:
            return None
        totals, _ = daylight([dict(dep=clock,arr=clock+duration)],request)
        # Only whole intervening days are unconditionally ordinary. A later
        # leg's boarding day may still be the route's departure day.
        terminal=last['stop'] in self.destinations
        end_day=(clock+duration)//86400
        nights = frozenset(nights_for(clock,clock+duration))
        sleeper = sleeper and bool(nights) and len(nights)<=request.max_nights and arrival_allowed(last['arr'],request,terminal=last['stop'] in self.destinations)
        first_modes = ([False] if sleeper and departure_allowed(clock,clock+duration,request,first=True) else []) + ([True] if short else [])
        next_modes = ([False] if sleeper and departure_allowed(clock,clock+duration,request) else []) + ([True] if short else [])
        if not daylight_totals_allowed(totals,request,0,end_day,complete=terminal):first_modes=[]
        if request.limit_daylight:
            if any(v>daylight_limit_minutes(request)*60 for day,v in totals.items() if 0<day<end_day):next_modes=[]
            if terminal and request.last_daylight_limit is not None and totals.get(end_day,0)>request.last_daylight_limit*60:next_modes=[]
        if not first_modes and not next_modes:
            return None
        return dict(first_modes=first_modes,next_modes=next_modes,clock=clock,duration=duration,nights=nights,daylight=totals)

    def options(self, boarding, first, short_used):
        """Task-specific invariant filtering is cached separately from path state."""
        key = boarding, first, bool(short_used)
        if key not in self.prepared:
            values=[]
            for segment in self.segments(boarding):
                info=segment['_prepared']
                modes=info['first_modes' if first else 'next_modes']
                for mode in modes:
                    if mode and short_used:
                        continue
                    values.append((info['clock'],segment,mode))
            values.sort(key=lambda item:item[0])
            self.prepared[key]=([v[0] for v in values],values)
        return self.prepared[key]

    def connections(self, boarding, last, short_used, high, anchor):
        request=self.request
        for mode in (False,True):
            if mode and short_used:
                continue
            gap=connection_gap(request,last is None or last['to']==boarding,previous_short=bool(last and last['short']),next_short=mode)*60
            earliest=last['arr']+gap if last else 0
            if last and self.dated_options is not None:
                times,values=self.dated_options.get((anchor,boarding,int(bool(short_used)),mode),([],[]))
                for dep,segment,offset in values[bisect_left(times,earliest):]:
                    yield segment,mode,offset
            else:
                clocks,options=self.options(boarding,last is None,short_used)
                offsets=range(max(0,earliest//86400),high+1) if last else (0,)
                for offset in offsets:
                    clock_low=max(0,earliest-offset*86400) if last else 0
                    for _,segment,is_short in options[bisect_left(clocks,clock_low):]:
                        if is_short==mode:
                            yield segment,mode,offset

    def build_dated_reachability(self, request, destinations, cancel, progress):
        """Optimistic suffix oracle: exact dated connections, ignoring city visits.

        Since departures strictly increase, reverse departure order computes
        latest viable connection deadlines without revisiting path prefixes.
        Ignoring aggregate daylight, required cities and city-repeat constraints
        can only admit extra suffixes, never remove a feasible complete route.
        """
        if not request.departure_date or not request.consecutive:
            return None
        start=date.fromisoformat(request.departure_date).toordinal()
        # First boarding is day zero. N continuous sleeping nights cannot
        # extend past day N+1; an extra day keeps the bound conservative even
        # for midnight boarding and a trailing short high-speed leg.
        horizon_days=min(request.max_nights+2,max(0,self.last_day+self.maximum_offset//86400-start+1))
        horizon=horizon_days*86400
        instances=[]
        cities=defaultdict(list)
        allowed=set(request.allowed_stations)
        for sid,stop in self.stops.items():
            if stop['city'] and (not allowed or sid in allowed):
                cities[stop['city']].append(sid)
        for index,sid in enumerate(self.stops):
            check_cancel(cancel)
            if allowed and sid not in allowed:
                continue
            for segment in self.segments(sid):
                info=segment['_prepared']
                service_offset=segment['service_dep']//86400
                days=self.calendars[segment['service']]
                for offset in range(horizon_days):
                    dep=info['clock']+offset*86400
                    arr=dep+info['duration']
                    if arr>horizon or start+offset-service_offset not in days:
                        continue
                    for mode in info['next_modes']:
                        instances.append((dep,arr,sid,segment['to'],mode,tuple(n+offset for n in info['nights']) if not mode else (),segment,offset))
            if index%200==0:
                progress(stage='建立日期接续索引',current=index,total=len(self.stops))
        instances.sort(key=lambda item:item[0],reverse=True)
        deadlines={}
        reachable=defaultdict(list)
        states=((False,0),(False,1),(True,1))
        for anchor in (-1,0):
            for index,(dep,arr,boarding,alighting,mode,covered_nights,segment,offset) in enumerate(instances):
                if index%1000==0:
                    check_cancel(cancel)
                if covered_nights and (min(covered_nights)<anchor or max(covered_nights)>=anchor+request.max_nights):
                    continue
                if alighting in destinations and not (anchor+request.min_nights)*86400<arr<=(anchor+request.max_nights+1)*86400:
                    continue
                for previous_short,used in states:
                    if mode and used:
                        continue
                    following_used=int(bool(used or mode))
                    if alighting not in destinations and deadlines.get((anchor,alighting,mode,following_used),-1)<arr:
                        continue
                    if not previous_short:
                        reachable[anchor,boarding,used,mode].append((dep,segment,offset))
                    group=cities[self.stops[boarding]['city']] if self.stops[boarding]['city'] else [boarding]
                    for previous_station in group:
                        gap=connection_gap(request,previous_station==boarding,previous_short=previous_short,next_short=mode)*60
                        key=anchor,previous_station,previous_short,used
                        deadlines[key]=max(deadlines.get(key,-1),dep-gap)
        self.metrics['dated_connection_instances']=len(instances)
        self.metrics['dated_connection_deadlines']=len(deadlines)
        self.dated_options={}
        for key,values in reachable.items():
            values.sort(key=lambda item:item[0])
            self.dated_options[key]=([v[0] for v in values],values)
        self.metrics['reachable_connection_options']=sum(len(v) for v in reachable.values())
        return deadlines

    def build_through_services(self):
        # GTFS block_id is explicit same-vehicle evidence. Only join at the
        # same station with nondecreasing service-day offsets and intersecting
        # calendars. No fuzzy name, number or spatial joins.
        blocks = defaultdict(list)
        for trip in list(self.trips.values()):
            if trip['block'] and self.calls[trip['id']]:blocks[trip['block']].append(trip['id'])
        for block,members in blocks.items():
            self.resource_check()
            successors = defaultdict(list)
            for first in members:
                self.resource_check()
                a=self.calls[first][-1]
                for second in members:
                    if first==second:continue
                    b=self.calls[second][0]
                    if not a['arrival_time_issues'] and not b['departure_time_issues'] and a['stop']==b['stop'] and a['arr']<=b['dep'] and self.calls[first][0]['dep']<b['dep']:
                        common=self.calendars[self.trips[first]['service']] & self.calendars[self.trips[second]['service']]
                        # An intervening block trip prevents skipping it only on
                        # dates when it actually runs. Preserve other dates.
                        for mid in members:
                            if mid not in (first,second) and self.calls[mid][0]['dep']>a['arr'] and self.calls[mid][-1]['arr']<b['dep']:
                                common=common-self.calendars[self.trips[mid]['service']]
                        if common:successors[first].append((second,common))
            chains=[]
            def extend(chain,days):
                self.resource_check()
                if len(chain)>1:chains.append((chain,days))
                for nxt,edge_days in successors[chain[-1]]:
                    common=days & edge_days
                    if nxt not in chain and common:
                        extend(chain+[nxt],common)

            for member in members:
                extend([member],self.calendars[self.trips[member]['service']])
            for chain,days in chains:
                identity='through:'+hashlib.sha256(encode(chain).encode()).hexdigest()[:24]
                calls=[];boundaries=[]
                for trip_id in chain:
                    part=[dict(c) for c in self.calls[trip_id]]
                    if calls:
                        boundaries.append(len(calls)-1)
                        calls[-1]['dep']=part[0]['dep'];calls[-1]['pickup']=part[0]['pickup']
                        calls[-1]['departure_time_issues']=part[0]['departure_time_issues']
                        part=part[1:]
                    calls.extend(part)
                for i,c in enumerate(calls):c['trip']=identity;c['seq']=i
                self.calls[identity]=calls
                self.join_boundaries[identity]=boundaries
                self.calendars[identity]=days
                self.trips[identity]=dict(self.trips[chain[0]],id=identity,service=identity,
                                          name='/'.join(self.trips[t]['name'] for t in chain),through_trips=chain)
                for c in calls:
                    if c['pickup']!=1 and not c['departure_time_issues']:self.by_stop[c['stop']].add(identity)

    def close(self):
        self.segments.cache_clear()
        self.db.close()

    def resolve(self, value, kind):
        if kind == 'city':
            matches = {key for key, stop in self.stops.items() if stop['city'] == value}
        elif value in self.stops:
            matches = {value}
        else:
            matches = {key for key, stop in self.stops.items() if stop['name'] == value}
            if len(matches) > 1:
                raise ValueError(f'站名 {value} 对应多个身份，请在地点选择中使用站点 ID')
        if not matches:
            raise ValueError(f'未找到{value}。城市需要明确归属；可在数据中心导入站点城市映射。')
        return matches

    @lru_cache(maxsize=30000)
    def segments(self, origin):
        result = []
        for trip_id in sorted(self.by_stop[origin]):
            self.resource_check()
            trip = self.trips[trip_id]
            calls = self.calls[trip_id]
            for i, first in enumerate(calls):
                if first['stop'] != origin or first['pickup'] == 1 or first['departure_time_issues']:
                    continue
                for j in range(i+1, len(calls)):
                    last = calls[j]
                    if trip_id in self.join_boundaries and not all(i < b < j for b in self.join_boundaries[trip_id]):
                        continue
                    if last['dropoff'] == 1 or last['arrival_time_issues']:
                        continue
                    if last['arr'] <= first['dep']:
                        continue
                    info=None
                    if self.request:
                        if self.request.allowed_stations and last['stop'] not in self.request.allowed_stations:
                            continue
                        covered=[self.trips[t]['type'] for t in trip.get('through_trips',[trip_id])]
                        if any(transport_category(t)=='non_rail' for t in covered):
                            self.metrics['known_non_rail_transport']+=1
                            continue
                        info=self.segment_modes(trip,first,last)
                        self.metrics['station_pairs_checked']+=1
                        if info is None:
                            self.metrics['invariant_pairs_rejected']+=1
                            continue
                    part = calls[i:j+1]
                    cities = []
                    for c in part:
                        city = self.stops[c['stop']]['city']
                        key = city or '@' + c['stop']
                        if not cities or cities[-1] != key:
                            cities.append(key)
                    edges = list(zip([c['stop'] for c in part], [c['stop'] for c in part][1:]))
                    if self.request and (len(cities)!=len(set(cities)) or set(cities) & set(self.request.forbidden_cities)):
                        self.metrics['invariant_city_rejected']+=1
                        continue
                    if self.request and (self.origin_key in cities[1:] or self.destination_key in cities and cities[-1]!=self.destination_key):
                        self.metrics['proven_origin_or_destination_city_dead_end']+=1
                        continue
                    result.append({'trip': trip_id, 'train': trip['name'], 'route_type': trip['type'],
                                   'covered_route_types': [self.trips[t]['type'] for t in trip.get('through_trips',[trip_id])],
                                   'service': trip['service'], 'block': trip['block'], 'through_trips': trip.get('through_trips', [trip_id]),
                                   'from': origin, 'to': last['stop'], 'service_dep': first['dep'],
                                   'service_arr': last['arr'], 'cities': cities, 'edges': edges,
                                   'transport_category': 'non_rail' if any(transport_category(self.trips[t]['type'])=='non_rail' for t in trip.get('through_trips',[trip_id])) else 'rail' if all(transport_category(self.trips[t]['type'])=='rail' for t in trip.get('through_trips',[trip_id])) else 'unknown',
                                   'conditional': first['pickup'] in (2,3) or last['dropoff'] in (2,3),
                                   'interpolated': any(c['exact'] == 0 for c in part),
                                   'timetable_issues':[issue for c in part[1:-1] for field in ('arrival_time_issues','departure_time_issues') for issue in c[field]],
                                   'unused_endpoint_time_issues':list(first['arrival_time_issues'])+list(last['departure_time_issues']),
                                   'unknown_cities': any(self.stops[c['stop']]['city'] is None for c in part)})
                    if info is not None:
                        result[-1]['_prepared']=info
        return result


def search(store, run_id, progress=lambda **kw: None, cancel=lambda: False):
    started=time.perf_counter()
    run = store.rows('SELECT * FROM runs WHERE id=?', (run_id,))[0]
    request = Request(**json.loads(run['request'])).validate()
    if request.rules_version != RULES_VERSION:
        raise ValueError('此任务使用旧版检索规则。请新建检索任务；旧检查点不能与当前规则混用。')
    from ..resources import SearchGuard,ResourceLimit
    guard=SearchGuard(store,request)
    user_cancel=cancel
    def cancel():
        if user_cancel():return True
        guard.check()
        return False
    snap = store.snapshot(run['snapshot'])
    # Freeze explicit manual mappings in task checkpoint on first execution.
    checkpoint = json.loads(run['checkpoint'])
    if 'mappings' not in checkpoint:
        checkpoint['mappings'] = store.setting('city_mapping:' + snap['id'], {})
    if 'evidence' not in checkpoint:
        checkpoint['evidence'] = [json.loads(r['payload']) for r in store.rows("SELECT payload FROM evidence WHERE snapshot=? AND kind IN ('fare','sleeper','track') ORDER BY created,id", (snap['id'],))]
    network = Network(snap, checkpoint['mappings'],lambda:check_cancel(cancel))
    reasons = Counter(json.loads(run['audit']).get('rejection_states', {}))
    visited = json.loads(run['audit']).get('visited_states', 0)
    completed = set(checkpoint.get('completed_partitions', []))
    completed_roots = set(checkpoint.get('completed_root_partitions', []))
    origins = sorted(network.resolve(request.origin, request.origin_kind))
    destinations = network.resolve(request.destination, request.destination_kind)
    for city in request.required_cities + request.forbidden_cities:
        if not any(s['city'] == city for s in network.stops.values()):
            network.close()
            raise ValueError(f'约束城市 {city} 没有明确归属站点，不能静默忽略；请先导入映射')
    allowed = set(request.allowed_stations)
    if allowed - network.stops.keys():
        network.close()
        raise ValueError('限定允许站点须使用本快照中的站点 ID')
    network.configure(request,destinations,origins)
    selected_day=date.fromisoformat(request.departure_date).toordinal() if request.departure_date else None
    city_stations=defaultdict(list)
    for sid,stop in network.stops.items():
        if stop['city']:
            city_stations[stop['city']].append(sid)
    pending = []
    evidence_index=index_evidence(checkpoint['evidence'])
    state = 'running'
    deadlines=None
    resource_stop=None
    dead_states=set()
    produced=0
    deleted_ids={row['result_id'] for row in store.rows('SELECT result_id FROM deleted_results WHERE run_id=?',(run_id,))}
    deleted_patterns={row['pattern'] for row in store.rows('SELECT pattern FROM deleted_patterns WHERE run_id=?',(run_id,))}
    writer_db=None
    writer_context=None

    def flush():
        nonlocal pending
        if not pending and not shared_writer.pending:
            return
        with writer_db:
            shared_writer.flush(writer_db)
            writer_db.executemany('INSERT OR IGNORE INTO results VALUES(?,?,?,?,?,?,?,?,?)', sorted(pending,key=lambda row:row[0]))
            from ..fare_cache import index_results
            # Finish this atomic result batch even when cancellation is pending;
            # its index and fragments must commit with the saved candidates.
            index_results(writer_db,run_id)
        shared_writer.committed()
        pending = []

    known_ids=set()
    patterns=set()
    result_states=Counter()
    missing_evidence=defaultdict(set)
    # Stream in insertion order. Resolve each missing-evidence fragment once,
    # instead of expanding complete legs millions of times during recovery.
    try:
        with store.connect() as db:
            shared_writer=SharedWriter(db,run_id)
            references=set()
            loaded=0
            for row in db.execute('SELECT id,pattern,status,payload FROM results INDEXED BY results_run_stream WHERE run_id=?',(run_id,)):
                if loaded%5000==0:
                    check_cancel(cancel)
                    if loaded:
                        progress(stage='恢复已保存候选',current=loaded)
                known_ids.add(row['id']);patterns.add(row['pattern']);result_states[row['status']]+=1
                saved=json.loads(unpack(row['payload']))
                if saved.get('__railnomad_shared')==1:
                    references.update(key['$shared'] for key in saved.get('missing_evidence_keys',[]))
                else:
                    for key in saved.get('missing_evidence_keys',[]):
                        missing_evidence[key[0]].add(tuple(key))
                loaded+=1
            for reference in references:
                key=shared_writer.missing_parts[reference]
                missing_evidence[key[0]].add(key)
    except (Cancelled,ResourceLimit) as exc:
        previous=json.loads(run['audit'])
        previous.update(status='limited' if isinstance(exc,ResourceLimit) else 'cancelled',summary_semantics='previous saved summary; recovery stopped before enumeration')
        if isinstance(exc,ResourceLimit):previous.update(resource_stop=str(exc),resource_limits=guard.cap)
        with store.connect() as db:
            db.execute('UPDATE runs SET status=?,audit=? WHERE id=?',(previous['status'],encode(previous),run_id))
        network.close()
        if isinstance(exc,ResourceLimit):return previous
        raise
    last_save=0.0
    last_emit=0.0

    def audit():
        return {'algorithm': 'dated-index-exact-v5', 'app_version': __version__, 'visited_states': visited, 'state_count_semantics': 'actual visits including unfinished-root replay on resume',
                'user_deleted_candidates':len(deleted_ids),'user_deleted_patterns':len(deleted_patterns),
                'completed_root_partitions': len(completed_roots), 'complete_candidates': len(known_ids),
                'daylight_limit_enabled':request.limit_daylight,'max_daylight_on_train':daylight_limit_minutes(request) if request.limit_daylight else None,
                'first_daylight_limit':request.first_daylight_limit,'last_daylight_limit':request.last_daylight_limit,
                'result_states':dict(result_states),'missing_evidence_keys':{k:len(v) for k,v in missing_evidence.items()},
                'city_combinations':len(patterns), 'summary_semantics':'incremental distinct result and evidence keys; reconstructed once on resume',
                'rejection_states': dict(reasons), 'rejected_full_routes': 'unknown (prefix constraints)',
                'completed_partitions': sorted(completed), 'total_partitions': len(origins),
                'calendar_boundary': [date.fromordinal(network.first_day).isoformat(), date.fromordinal(network.last_day).isoformat()],
                'maximum_service_time_seconds': network.maximum_offset,
                'coverage': {'schedule': 'snapshot_only', 'quarantined_trips': json.loads(snap['quality']).get('quarantined_trips', []), 'city': 'pending_if_missing',
                             'repaired_trips':json.loads(snap['quality']).get('repaired_trips',[]),
                             'time_affected_trips':json.loads(snap['quality']).get('time_affected_trips',[]),
                             'timetable_policy':json.loads(snap['quality']).get('timetable_policy','whole_trip_quarantine'),
                             'unusable_time_events':json.loads(snap['quality']).get('unusable_time_events',0),
                             'sleeper': 'pending', 'fare': 'pending', 'physical_track': 'pending',
                             'student_eligibility': 'pending', 'availability': 'pending'},
                'status': state, 'resource_limits':guard.cap,'resource_stop':resource_stop, 'snapshot': snap['id'], 'rules': request.rules_version, 'departure_date': request.departure_date, 'preparation': dict(network.metrics), 'storage': 'shared-compressed-v2',
                'current_attempt_seconds':time.perf_counter()-started,'shared_fragments':len(shared_writer.fingerprints)}

    def save():
        nonlocal last_save
        if state=='running' and time.monotonic()-last_save<3:
            return
        last_save=time.monotonic()
        flush()
        checkpoint['completed_partitions'] = sorted(completed)
        checkpoint['completed_root_partitions'] = sorted(completed_roots)
        with store.connect() as db:
            db.execute('UPDATE runs SET status=?,checkpoint=?,audit=? WHERE id=?',
                       (state, encode(checkpoint), encode(audit()), run_id))

    def emit():
        nonlocal last_emit
        last_emit=time.monotonic()
        progress(stage='精确枚举', visited=visited,
                 routes=len(known_ids),
                 partitions=len(completed), total_partitions=len(origins))

    def output(legs, feasible, nights, cities, edges, short_used):
        nonlocal produced
        if request.consecutive and uncovered_nights(legs[0]['dep'], legs[-1]['arr'], nights):
            reasons['uncovered_overnight'] += 1
            return
        stop_ids = [legs[0]['from']] + [l['to'] for l in legs]
        known_cities = [network.stops[sid]['city'] for sid in stop_ids if network.stops[sid]['city']]
        unknown = any(l['unknown_cities'] for l in legs)
        missing_required = set(request.required_cities) - set(known_cities)
        if request.required_city_mode=='OR' and set(request.required_cities) & set(known_cities):
            missing_required=set()
        if missing_required and not unknown:
            reasons['missing_required_city'] += 1
            return
        totals, _ = daylight(legs, request)
        pattern = []
        for station in [legs[0]['from']] + [l['to'] for l in legs]:
            city = network.stops[station]['city'] or '站:' + network.stops[station]['name'] + '[' + station + ']'
            if not pattern or pattern[-1] != city:
                pattern.append(city)
        key = {'legs': [{k: l[k] for k in ('trip', 'from', 'to', 'dep', 'arr', 'short')} for l in legs], 'dates': sorted(feasible)}
        identity = hashlib.sha256((run_id + encode(key)).encode()).hexdigest()
        if identity in deleted_ids or encode(pattern) in deleted_patterns:
            reasons['user_deleted_candidate_or_pattern']+=1
            return
        if identity in known_ids:
            produced+=1
            return
        guard.check_count(len(known_ids))
        gaps = sorted(nights_for(legs[0]['dep'],legs[-1]['arr']) - nights)
        payload = {'id': identity, 'run_id': run_id, 'snapshot': snap['id'], 'pattern': pattern,
                   'legs': legs, 'nights': len(nights), 'night_indices': sorted(nights),
                   'empty_nights': gaps, 'dates': [date.fromordinal(d).isoformat() for d in sorted(feasible)],
                   'daylight_seconds': totals, 'known_price': None, 'status': 'pending',
                   'pending': ['sleeper', 'fare', 'physical_track', 'availability', 'student_eligibility'] +
                              (['city_membership'] if unknown else []) + (['required_city_unverified'] if missing_required else []),
                   'track_status': 'station_sequence_proxy', 'changes': sum(a['to'] != b['from'] for a,b in zip(legs, legs[1:])),
                   'relaxation_minutes': sum(l['departure_relaxation_minutes']+l['arrival_relaxation_minutes'] for l in legs),
                   'comfort_minutes':sum(l['comfort_departure_minutes']+l['comfort_arrival_minutes'] for l in legs),
                   'min_daylight_off_minutes': min((request.daylight_end-request.daylight_start-seconds/60 for seconds in totals.values()), default=request.daylight_end-request.daylight_start),
                   'duration_seconds': legs[-1]['arr']-legs[0]['dep'],
                   'preference_score': sum(c in request.preferred_cities for c in pattern) + int(legs[0]['from'] in request.preferred_stations) + int(legs[-1]['to'] in request.preferred_stations),
                   'short_hsr': short_used, 'source': 'gtfs_schedule', 'simulated': json.loads(snap['metadata']).get('source') == 'simulated'}
        variants, missing = evaluate(payload, checkpoint['evidence'], request.budget,evidence_index=evidence_index)
        payload['date_evidence_variants'] = variants
        payload['missing_evidence_keys'] = missing
        payload['pending'] = sorted({('physical_track' if key[0]=='track' else key[0]) for key in missing} | {'availability','student_eligibility'} | ({'city_membership'} if unknown else set()) | ({'required_city_unverified'} if missing_required else set()) | ({'transport_category'} if any(l['transport_category']=='unknown' for l in legs) else set()) | ({'conditional_boarding_or_interpolated_time'} if any(l['conditional'] or l['interpolated'] for l in legs) else set()))
        payload['track_status'] = 'verified_track' if variants and all(v['checks']['physical_track']=='passed' for v in variants) else 'station_sequence_proxy'
        states = {v['status'] for v in variants}
        payload['status'] = 'failed' if states == {'failed'} else 'passed' if states == {'passed'} else 'pending'
        if any(l.get('timetable_issues') for l in legs):
            payload['pending'].append('intermediate_timetable')
            if payload['status']=='passed':payload['status']='pending'
        prices = [v['price_high'] for v in variants]
        price = min(prices) if prices and all(p is not None for p in prices) else None
        known_prices = [v['price_high'] for v in variants if v['price_high'] is not None]
        payload['price_range'] = [min(v['price_low'] for v in variants if v['price_low'] is not None), max(known_prices)] if known_prices else None
        payload['price_coverage_complete'] = all(p is not None for p in prices)
        payload['known_price'] = price
        if any((b,a) in set(edges) for a,b in edges):
            payload['pending'].append('reverse_station_sequence_proxy')
        encoded_payload=pack(encode(shared_writer.compact(payload)))
        known_ids.add(identity)
        patterns.add(encode(pattern))
        result_states[payload['status']]+=1
        for key in missing:
            missing_evidence[key[0]].add(tuple(key))
        pending.append((identity, run_id, encode(pattern), len(nights), price, payload['changes'], short_used, payload['status'], encoded_payload))
        produced+=1
        if len(pending) >= 5000:
            flush()
        guard.check_count(len(known_ids))

    def dfs(legs, feasible, nights, cities, edges, short_used, daytime=None):
        nonlocal visited
        check_cancel(cancel)
        visited += 1
        if visited % 500 == 0 and time.monotonic()-last_emit>=0.5:
            emit()
        last = legs[-1] if legs else None
        if last and last['to'] in destinations:
            if request.min_nights <= len(nights) <= request.max_nights:
                output(legs, feasible, nights, cities, edges, short_used)
            return
        if len(nights) > request.max_nights:
            return
        if last and deadlines is not None and deadlines.get((legs[0]['dep']//86400-(legs[0]['dep']%86400<21600),last['to'],last['short'],int(bool(short_used))),-1)<last['arr']:
            reasons['dated_suffix_unreachable'] += 1
            return
        state_key=None
        if last:
            stops_seen=frozenset(network.stops[l['to']]['city'] for l in legs) | {network.stops[legs[0]['from']]['city']}
            first_dep=legs[0]['dep']
            state_key=(last['to'],last['arr'],last['short'],short_used,frozenset(nights),frozenset(cities),stops_seen,
                       frozenset(feasible),tuple(sorted((d,n) for d,n in (daytime or {}).items() if n)),first_dep//86400-(first_dep%86400<21600),first_dep//86400)
            if state_key in dead_states:
                reasons['proven_dead_state_reused']+=1
                return
        before=produced
        origin = partition if last is None else last['to']
        stations = city_stations.get(network.stops[origin]['city'],[origin]) if last else [origin]
        for boarding in stations:
            if allowed and boarding not in allowed:
                continue
            high=0
            if last:
                high=network.last_day+network.maximum_offset//86400-min(feasible)
                if request.consecutive:
                    high=min(high,request.max_nights+1)
                    if nights:
                        high=min(high,max(nights)+2)
            first_dep=legs[0]['dep'] if legs else 0
            anchor=first_dep//86400-(first_dep%86400<21600)
            for segment,mode,offset in network.connections(boarding,last,short_used,high,anchor):
                check_cancel(cancel)
                info=segment['_prepared']
                dep=info['clock']+offset*86400
                arr=dep+info['duration']
                if deadlines is not None and segment['to'] not in destinations and deadlines.get(((legs[0]['dep'] if legs else dep)//86400-((legs[0]['dep'] if legs else dep)%86400<21600),segment['to'],mode,int(bool(short_used or mode))),-1)<arr:
                    reasons['dated_suffix_unreachable'] += 1
                    continue
                service_offset=segment['service_dep']//86400
                if selected_day is not None:
                    if selected_day+offset-service_offset not in network.calendars[segment['service']]:
                        reasons['incompatible_calendar'] += 1
                        continue
                    common=feasible if feasible is not None else {selected_day}
                else:
                    key=segment['service'],service_offset-offset
                    if key not in network.shifted_calendars:
                        network.shifted_calendars[key]={day+service_offset-offset for day in network.calendars[segment['service']]}
                    dates=network.shifted_calendars[key]
                    common=dates if feasible is None else feasible & dates
                    if not common:
                        reasons['incompatible_calendar'] += 1
                        continue
                added_nights={n+offset for n in info['nights']} if not mode else set()
                combined_nights=nights | added_nights
                if len(combined_nights)>request.max_nights:
                    reasons['night_count'] += 1
                    continue
                if request.consecutive and combined_nights:
                    if len(combined_nights)!=max(combined_nights)-min(combined_nights)+1:
                        reasons['nonconsecutive'] += 1
                        continue
                    first_dep=legs[0]['dep'] if legs else dep
                    first_night=first_dep//86400-(first_dep%86400<21600)
                    last_closed=(arr-21600)//86400-1
                    if last_closed>=first_night and (min(combined_nights)>first_night or max(combined_nights)<last_closed):
                        reasons['uncovered_overnight'] += 1
                        continue
                merged=list(cities)
                bad=False
                for city in segment['cities']:
                    if merged and city==merged[-1]:
                        continue
                    if city in merged:
                        bad=True
                        break
                    merged.append(city)
                if bad:
                    reasons['city_repeat_or_forbidden'] += 1
                    continue
                next_daytime=dict(daytime or {})
                for day,seconds in info['daylight'].items():
                    day+=offset
                    next_daytime[day]=next_daytime.get(day,0)+seconds
                if not daylight_totals_allowed(next_daytime,request,(legs[0]['dep'] if legs else dep)//86400,arr//86400,complete=segment['to'] in destinations):
                    reasons['daylight_exceeded'] += 1
                    continue
                root_key=hashlib.sha256(encode([partition,segment['trip'],boarding,segment['to'],segment['service_dep'],segment['service_arr'],mode]).encode()).hexdigest() if not last else None
                if root_key and root_key in completed_roots:
                    continue
                leg_key=(segment['trip'],boarding,segment['to'],segment['service_dep'],segment['service_arr'],offset,mode,last is None)
                leg=network.leg_cache.get(leg_key)
                if leg is None:
                    leg=dict((k,v) for k,v in segment.items() if not k.startswith('_'))
                    leg.update(dep=dep,arr=arr,short=mode,service_day_offset=offset-service_offset,
                               from_name=network.stops[boarding]['name'],to_name=network.stops[segment['to']]['name'],
                               sleeper='not_required' if mode else 'unknown',price=None,
                               train_night=train_night_for_leg(segment,network.calls[segment['trip']],short=mode),
                               departure_relaxation_minutes=0 if not last or mode else window_relaxation(dep,request.target_depart_start,request.target_depart_end),
                               arrival_relaxation_minutes=0 if segment['to'] in destinations or mode else window_relaxation(arr,request.target_arrive_start,request.target_arrive_end),
                               train_night_status='not_applicable' if mode else 'unknown_through_origin' if segment['block'] else 'service_schedule')
                    comfort=comfort_for_leg(leg,request)
                    if not mode and leg['train_night'] is None and not segment['block']:leg['train_night_status']='unknown_timetable_origin'
                    leg.update(comfort_departure_minutes=comfort['departure'],comfort_arrival_minutes=comfort['arrival'])
                    network.leg_cache[leg_key]=leg
                    if len(network.leg_cache)>100_000:network.leg_cache.clear()
                dfs(legs+[leg],common,combined_nights,merged,edges | set(segment['edges']),short_used+mode,next_daytime)
                if root_key:
                    completed_roots.add(root_key)
                    save()
        if state_key is not None and produced==before:
            dead_states.add(state_key)
            if len(dead_states)>100_000:dead_states.clear()
    try:
        writer_context=store.connect()
        writer_db=writer_context.__enter__()
        writer_db.execute('PRAGMA cache_size=-262144')
        # A checkpoint uses a separate FULL-synchronous catalog transaction,
        # durably committing results with each saved root/cancel/completion.
        writer_db.execute('PRAGMA synchronous=NORMAL')
        guard.check(force=True)
        guard.check_count(len(known_ids))
        save()
        deadlines=network.build_dated_reachability(request,destinations,cancel,progress)
        for partition in origins:
            if partition in completed:
                continue
            save()
            dfs([], None, set(), [], set(), 0)
            completed.add(partition)
            checkpoint.pop('partition_start', None)
            save()
            emit()
        state = 'completed'
        save()
        return audit()
    except ResourceLimit as exc:
        state='limited';resource_stop=str(exc)
        save()
        return audit()
    except Cancelled:
        state = 'cancelled'
        save()
        raise
    except Exception:
        state = 'failed'
        save()
        raise
    finally:
        if writer_db is not None:
            writer_context.__exit__(None,None,None)
        network.close()

