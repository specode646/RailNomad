"""Dated public fare lookup and auditable student estimates, separate from frozen search."""
import hashlib
import json
import os
import re
import time
import urllib.error
from datetime import datetime,timezone,timedelta
from decimal import Decimal,ROUND_HALF_UP
from .storage import encode,now
from .evidence import boarding_date, fare_covers_leg,index_evidence
from .data.release import check_cancel,RetryLater

POLICY='https://kyfw.12306.cn/otn/gonggao/student.html'
RULE='12306-student-2025-09-execution-v1'


def amount(value):
    if isinstance(value,bool):return None
    if isinstance(value,(int,float)):value=str(value)
    if not isinstance(value,str):return None
    match=re.fullmatch(r'\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*',value)
    return Decimal(match[1]) if match else None


def estimate(adult, *, seat, emu, hard_seat=None, published=None):
    """Missing EMU published fare yields an interval covering the 40% floor."""
    adult=amount(adult)
    if adult is None:return None
    if emu:
        published=amount(published)
        discounted=adult*Decimal('.75')
        if published is None:
            low,high=discounted,adult
            formula='执行价 × 75%；缺公布价，按 40% 下限规则保留至成人价的区间'
        else:
            low=high=min(adult,max(discounted,published*Decimal('.4')))
            formula='min(执行价, max(执行价 × 75%, 公布价 × 40%))'
    elif seat=='硬卧':
        base=amount(hard_seat)
        if base is None or base>adult:return None
        low=high=adult-base*Decimal('.5')
        formula='成人硬卧价 − 同区间硬座价 × 50%'
    elif seat=='硬座':
        low=high=adult*Decimal('.5');formula='成人硬座价 × 50%'
    else:
        return None
    return dict(low=float(low.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)),
                high=float(high.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)),
                formula=formula,rule_version=RULE,estimated=True,policy=POLICY,
                rounding='参考估算四舍五入至分，实际票面舍入以12306为准',
                conditions='须满足学生资质、优惠区间、次数及合理径路；未核实资格和余票')


def parse_prices(*args, **kwargs):
    raise ValueError('公开发行版不提供自动核价')


class PublicFareClient:
    def __init__(self, *args, **kwargs):
        raise ValueError('公开发行版不提供自动核价，请手工导入票价证据或到官方页面核验')


def saved_assessment(store,identity):
    rows=store.rows('SELECT payload FROM fare_assessments WHERE result_id=?',(identity,))
    return json.loads(rows[0]['payload']) if rows else None


def fare_index(store,snapshot):
    records=[json.loads(row['payload']) for row in store.rows("SELECT payload FROM evidence WHERE snapshot=? AND kind='fare'",(snapshot,))]
    return index_evidence(records)


def applicable_fares(fares,leg):
    return [fare for fare in fares if fare['seat']!='硬座' and fare_covers_leg({'seat':fare['seat']},leg)]


def normalize_fares(records,leg):
    """Pair matching observations, then prefer the latest quote for each berth."""
    groups={};bases={}
    for record in records:
        if 'passenger' not in record or 'low' not in record:continue
        origin=(record.get('observed_at',''),record.get('source',''))
        if record.get('seat')=='硬座' and record['passenger']=='adult':bases[origin]=record['low']
        key=(record['seat'],record['berth'],*origin)
        quote=groups.setdefault(key,dict(seat=record['seat'],berth=record['berth'],adult=None,
                    observed_at=origin[0],source=origin[1],student=None))
        if record['passenger']=='adult':
            quote.update(adult=record['low'],adult_high=record['high'])
        else:
            quote['student']={k:v for k,v in record.items() if k not in ('kind','trip','from','to','date','seat','berth','passenger')}
    latest={}
    for key,quote in groups.items():
        if quote['student'] is None and quote['adult'] is not None and quote['adult']==quote.get('adult_high'):
            quote['student']=estimate(quote['adult'],seat=quote['seat'],emu=bool(re.match(r'^[GDC]',leg['train'])),hard_seat=bases.get((quote['observed_at'],quote['source'])))
        slot=key[:2]
        stamp=quote['observed_at']
        try:stamp=datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat()
        except ValueError:pass
        if slot not in latest or stamp>latest[slot][0]:latest[slot]=(stamp,quote)
    return applicable_fares([entry[1] for entry in latest.values()],leg)


def resolved_assessment(payload,index,existing=None):
    result={'dates':{}}
    frozen={v['start_date']:v for v in payload.get('date_evidence_variants',[])}
    for start in payload['dates']:
        old=(existing or {}).get('dates',{}).get(start,{})
        details=[];observed=[]
        for position,leg in enumerate(payload['legs']):
            day=boarding_date(start,leg['dep']//86400)
            key=('fare',leg['trip'],leg['from'],leg['to'],day)
            records=list(index.get(key,[]))
            frozen_legs=frozen.get(start,{}).get('legs',[])
            if position<len(frozen_legs):records+=frozen_legs[position].get('fares',[])
            old_legs=old.get('legs',[])
            saved=old_legs[position] if position<len(old_legs) else {}
            for quote in saved.get('fares',[]):
                base={k:quote.get(k,'') for k in ('seat','berth','source','observed_at')}
                if quote.get('adult') is not None:records.append(dict(base,passenger='adult',low=quote['adult'],high=quote.get('adult_high',quote['adult'])))
                if quote.get('student'):records.append(dict(quote['student'],**base,passenger='student'))
            quotes=normalize_fares(records,leg)
            observed += [q['observed_at'] for q in quotes if q.get('observed_at')]
            details.append(dict(fares=quotes,boarding_date=day,error=saved.get('error','此区间、登车日尚无可用报价') if not quotes else ''))
        result['dates'][start]=dict(legs=details,queried_at=max(observed,default=old.get('queried_at',now())),kind='reused')
    return result


def read_assessment(store,identity):
    rows=store.rows('SELECT payload FROM results WHERE id=?',(identity,))
    if not rows:return None
    payload=json.loads(rows[0]['payload'])
    from .fare_cache import payload_keys,targeted_index
    with store.connect() as db:index=targeted_index(db,payload['snapshot'],payload_keys(payload))
    return resolved_assessment(payload,index,saved_assessment(store,identity))


def fare_signature(store,snapshot,run_id=None):
    from .fare_cache import revision
    with store.connect() as db:return revision(db,snapshot,run_id)


def sync_run_fares(store,run_id,progress=lambda **kw:None,cancel=lambda:False):
    """Update only routes reached through changed quote keys or new postings."""
    from .fare_cache import VERSION,index_results,affected_results,payload_keys,targeted_index
    run=store.rows('SELECT snapshot FROM runs WHERE id=?',(run_id,))[0]
    signature=fare_signature(store,run['snapshot'],run_id);key='fare_sync:'+run_id
    previous=store.setting(key)
    if previous==signature:return {'updated':0,'checked':0,'unchanged':True}
    after=previous[1] if previous and previous[0]==VERSION else 0
    old_rowid=previous[2] if previous and previous[0]==VERSION else None
    with store.connect() as db:
        _,_,indexed=index_results(db,run_id,cancel,progress)
        identities=affected_results(db,run_id,after,signature[1],old_rowid)
    total=len(identities);updated=0;checked=0;cache={}
    with store.connect() as db:
        for result_key in sorted(identities):
            check_cancel(cancel)
            row=db.execute('SELECT r.id,r.payload FROM fare_dependency_results m JOIN results r ON r.id=m.result_id WHERE m.id=?',(result_key,)).fetchone()
            identity=row['id']
            payload=store.expand_payload(row['payload'],db)
            index=targeted_index(db,run['snapshot'],payload_keys(payload),cache)
            saved=db.execute('SELECT payload FROM fare_assessments WHERE result_id=?',(identity,)).fetchone()
            existing=json.loads(saved['payload']) if saved else None
            assessment=resolved_assessment(payload,index,existing)
            matched=any(applicable_fares(detail['fares'],leg) for instance in assessment['dates'].values() for leg,detail in zip(payload['legs'],instance['legs']))
            if matched or saved:
                ranges=[totals(payload,instance['legs']) for instance in assessment['dates'].values()]
                low=min(r[0] for r in ranges)
                high=min(r[1] for r in ranges) if all(r[1] is not None for r in ranges) else None
                db.execute('''INSERT INTO fare_assessments VALUES(?,?,?,?,?)
                    ON CONFLICT(result_id) DO UPDATE SET low=excluded.low,high=excluded.high,updated=excluded.updated''',
                    (identity,encode({'shared_cache':True}),low,high,now()))
            updated+=bool(matched);checked+=1
            if checked%500==0:progress(stage='更新受影响方案的预算',current=checked,total=total,matched=updated)
        check_cancel(cancel)
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,encode(signature)))
    progress(stage='更新受影响方案的预算',current=checked,total=total,matched=updated)
    return {'updated':updated,'checked':checked,'total':total,'indexed':indexed}


def price_route(*args, **kwargs):
    raise ValueError('公开发行版不提供自动核价')


def price_routes(*args, **kwargs):
    raise ValueError('公开发行版不提供自动核价')


def totals(payload,details):
    low=Decimal(0);high=Decimal(0);complete=True
    for leg,detail in zip(payload['legs'],details):
        quotes=[f['student'] for f in detail.get('fares',[]) if f.get('student') and fare_covers_leg({'seat':f['seat']},leg)]
        if not quotes:complete=False;continue
        # Route remains feasible if at least one eligible seat fits budget.
        low+=min(Decimal(str(q['low'])) for q in quotes);high+=max(Decimal(str(q['high'])) for q in quotes)
    return float(low),float(high) if complete and len(details)==len(payload['legs']) else None


def save_assessment(store,payload,assessment):
    ranges=[totals(payload,d['legs']) for d in assessment['dates'].values()]
    all_dates=set(assessment['dates'])==set(payload['dates'])
    low=min(r[0] for r in ranges) if all_dates else 0
    high=min(r[1] for r in ranges) if all_dates and all(r[1] is not None for r in ranges) else None
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO fare_assessments VALUES(?,?,?,?,?)',(payload['id'],encode(assessment),low,high,now()))
        # Future searches can freeze the same dated observations. Existing task
        # checkpoints and result payloads remain unchanged.
        for start,instance in assessment['dates'].items():
            for leg,detail in zip(payload['legs'],instance['legs']):
                for quote in detail.get('fares',[]):
                    if not quote.get('observed_at'):continue
                    base=dict(kind='fare',source=quote['source'],observed_at=quote['observed_at'],
                              date=boarding_date(start,leg['dep']//86400),trip=leg['trip'],
                              **{'from':leg['from'],'to':leg['to']},seat=quote['seat'],berth=quote['berth'])
                    records=[dict(base,passenger='adult',low=quote['adult'],high=quote['adult'],source_type='observed')]
                    if quote.get('student'):
                        records.append(dict(base,**quote['student'],passenger='student',source_type='derived'))
                    for record in records:
                        identity=hashlib.sha256(encode([payload['snapshot'],record]).encode()).hexdigest()
                        db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)',(identity,payload['snapshot'],'fare',encode(record),now()))


def present_fares(payload,start,budget,assessment=None):
    from .ui.itinerary import money
    instance=resolved_assessment(payload,{},assessment)['dates'].get(start)
    if instance is None:return '此日期不适用',[],'请选择方案的实际出发日'
    details=instance['legs'];low,high=totals(payload,details);rows=[]
    for leg,detail in zip(payload['legs'],details):
        fares=applicable_fares(detail.get('fares',[]),leg)
        for fare in fares:
            student=fare.get('student')
            source=fare['source']+(' · '+student.get('formula','已保存学生参考价') if student else '')
            rows.append([f"{leg['train']} / {detail['boarding_date']}",f"{leg['from_name']} → {leg['to_name']}",f"{fare['seat']} / {fare['berth']}",money(fare['adult'],fare.get('adult_high')) if fare.get('adult') is not None else '—',money(student['low'],student['high']) if student else '待核价',source])
        if not fares:rows.append([f"{leg['train']} / {detail['boarding_date']}",f"{leg['from_name']} → {leg['to_name']}",'待核价','—','—',detail.get('error') or '此区间、登车日尚无可用报价'])
    known=sum(any(q.get('student') for q in applicable_fares(d.get('fares',[]),l)) for l,d in zip(payload['legs'],details))
    status='超出预算' if low>budget else '预算内（参考估算）' if high is not None and high<=budget else '整程待核价'
    shown=money(low,high) if high is not None else '已知部分 '+money(low) if known else '待核价'
    summary=f'学生参考 {shown}  ·  已覆盖 {known}/{len(payload["legs"])} 段  ·  {status}  ·  预算上限 ¥{budget:g}'
    timestamp=datetime.fromisoformat(instance['queried_at']).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    notice=f'最近报价 {timestamp}（北京时间）。复用同车次、同区间、同登车日已存报价；缺价不按零元。硬座不列入可选票价，仅作为硬卧折扣内部基数。学生资格和余票仍需核实。'
    return summary,rows,notice
