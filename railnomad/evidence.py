"""Explicit, dated manual evidence. No scraping, inferred discounts or invented prices."""
import json
import math
from decimal import Decimal
from datetime import date,datetime
from uuid import uuid4
from functools import lru_cache
from .storage import encode,now
from .data.release import check_cancel


def import_evidence(store,snapshot,path,progress=lambda **kw:None,cancel=lambda:False):
    with open(path,encoding='utf-8-sig') as stream:
        records=json.load(stream)
    if not isinstance(records,list):raise ValueError('证据文件应为 JSON 数组')
    valid=[]
    for index,record in enumerate(records):
        check_cancel(cancel)
        if index%1000==0:progress(stage='校验日期化证据',current=index,total=len(records))
        if record.get('kind') not in ('fare','sleeper','track'):
            raise ValueError('证据 kind 只能为 fare / sleeper / track')
        for key in ('source','observed_at','date','trip','from','to'):
            if not record.get(key):raise ValueError('证据缺少 '+key)
        date.fromisoformat(record['date'])
        if datetime.fromisoformat(record['observed_at']).tzinfo is None:
            raise ValueError('证据查询时间必须含时区')
        if record['kind']=='fare':
            if not record.get('seat') or not record.get('berth') or record.get('passenger') not in ('adult','student'):
                raise ValueError('票价须有席别、铺位、乘客类型')
            low,high=record.get('low'),record.get('high')
            if isinstance(low,bool) or isinstance(high,bool) or not isinstance(low,(int,float)) or not isinstance(high,(int,float)) or not math.isfinite(low) or not math.isfinite(high) or not 0<=low<=high:
                raise ValueError('价格区间必须为非负且下界不大于上界')
            if record.get('estimated') and not all(record.get(k) for k in ('formula','rule_version','conditions','rounding')):
                raise ValueError('估算学生价必须记录公式、规则版本、适用条件和舍入方式')
        elif record.get('status') not in ('passed','failed','pending'):
            raise ValueError('卧铺/物理区段证据须有 passed / failed / pending 状态')
        if record.get('seat_kind') not in (None,'sleeper','seated','unknown'):
            raise ValueError('seat_kind 只能为 sleeper / seated / unknown')
        if record.get('kind')=='track' and record.get('sections') is not None:
            sections=record['sections']
            if not isinstance(sections,list) or any(not isinstance(section,dict) or not section.get('id') or isinstance(section.get('direction'),bool) or section.get('direction') not in (-1,1) for section in sections):
                raise ValueError('物理区段须为包含 id 和 ±1 direction 的数组')
        record.setdefault('source_type','manual')
        if record['source_type'] not in ('observed','derived','manual','simulated','unknown'):
            raise ValueError('未知证据来源类型')
        valid.append(record)
    with store.connect() as db:
        for record in valid:
            check_cancel(cancel)
            db.execute('INSERT INTO evidence VALUES(?,?,?,?,?)',(uuid4().hex,snapshot,record['kind'],encode(record),now()))
    return len(valid)


def index_evidence(records):
    keyed={}
    for record in records:
        key=tuple(record[k] for k in ('kind','trip','from','to','date'))
        keyed.setdefault(key,[]).append(record)
    return keyed


@lru_cache(maxsize=8192)
def boarding_date(start,offset):
    from datetime import timedelta
    return (date.fromisoformat(start)+timedelta(days=offset)).isoformat()


def evaluate(payload,records,budget,*,evidence_index=None):
    """Evidence is keyed to exact boarding date and station pair; variants remain dated."""
    keyed=index_evidence(records) if evidence_index is None else evidence_index
    variants=[]
    missing=set()
    for start in payload['dates']:
        details=[];total_low=Decimal(0);total_high=Decimal(0);all_student=True;sleeper=True;track=True;failed=[];track_directions={}
        for i,leg in enumerate(payload['legs']):
            boarding=boarding_date(start,leg['dep']//86400)
            key=(leg['trip'],leg['from'],leg['to'],boarding)
            fares=keyed.get(('fare',)+key,[])
            student=[r for r in fares if r['passenger']=='student' and fare_covers_leg(r,leg)]
            # Preserve all dated price variants; aggregate a conservative interval.
            if student:
                total_low+=min(Decimal(str(r['low'])) for r in student);total_high+=max(Decimal(str(r['high'])) for r in student)
            else:
                all_student=False;missing.add(('fare',)+key)
            sleep=keyed.get(('sleeper',)+key,[]) if not leg['short'] else [{'status':'passed'}]
            tracks=keyed.get(('track',)+key,[])
            if any(r['status']=='failed' for r in sleep):failed.append('sleeper_failed')
            if any(r['status']=='failed' for r in tracks):failed.append('track_failed')
            if not sleep or any(r['status']!='passed' for r in sleep):sleeper=False;missing.add(('sleeper',)+key)
            if not tracks or any(r['status']!='passed' or not r.get('sections') for r in tracks):track=False;missing.add(('track',)+key)
            for record in tracks:
                if record['status']!='passed':continue
                for section in record.get('sections',[]):
                    sid,direction=section.get('id'),section.get('direction')
                    if not sid or direction not in (-1,1):
                        track=False;missing.add(('track',)+key);continue
                    if sid in track_directions and track_directions[sid]!=direction:
                        failed.append('reverse_verified_track')
                    track_directions[sid]=direction
            details.append({'leg':i,'boarding_date':boarding,'fares':fares,'sleeper_evidence':sleep,'track_evidence':tracks})
        budget_status='pending'
        if all_student:
            budget_status='passed' if total_high<=Decimal(str(budget)) else 'failed' if total_low>Decimal(str(budget)) else 'pending'
        if budget_status=='failed':failed.append('budget_exceeded')
        status='failed' if failed else 'passed' if all_student and budget_status=='passed' and sleeper and track and not any(l['unknown_cities'] or l['conditional'] or l['interpolated'] or l.get('transport_category')=='unknown' for l in payload['legs']) else 'pending'
        variants.append({'start_date':start,'price_low':float(total_low) if all_student else None,
                         'price_high':float(total_high) if all_student else None,'budget_status':budget_status,
                         'status':status,'reasons':failed,'checks':{'sleeper':'failed' if 'sleeper_failed' in failed else 'passed' if sleeper else 'pending', 'physical_track':'failed' if any(reason in failed for reason in ('track_failed','reverse_verified_track')) else 'passed' if track else 'pending','budget':budget_status},'legs':details})
    return variants,sorted(missing)


SLEEPER_SEATS = {'硬卧','软卧','高级软卧','动卧','二等卧','一等卧'}
SEATED_SEATS = {'硬座','软座','无座','站票','二等座','一等座','商务座','特等座'}


def fare_covers_leg(fare, leg):
    if leg['short']:
        return True
    if fare['seat'] in SEATED_SEATS:
        return False
    return fare.get('seat_kind') == 'sleeper' or fare['seat'] in SLEEPER_SEATS
