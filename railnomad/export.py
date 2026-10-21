"""Atomic, scoped, streaming exports with stable foreign keys."""
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from .query import where, validate_filters
from .domain.model import outdated_search_rules,daylight_rule_text,comfort_minutes,comfort_text,time_windows_text,timetable_warning
from .storage import encode,now
from .data.release import check_cancel
from .evidence import fare_covers_leg


def safe(value):
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r')):
        return "'"+value
    return value


def export_run(store,run_id,parent,filters,scope,progress=lambda **kw:None,cancel=lambda:False):
    from .resources import check_free
    check_free(parent)
    validate_filters(store,run_id,filters)
    from .fares import sync_run_fares,fare_index,resolved_assessment,applicable_fares
    sync_run_fares(store,run_id,progress,cancel)
    run=store.rows('SELECT * FROM runs WHERE id=?',(run_id,))[0]
    snap=store.snapshot(run['snapshot'])
    price_index=fare_index(store,snap['id'])
    name='RailNomad-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6]
    stage=Path(parent)/('.'+name+'.partial');stage.mkdir()
    target=Path(parent)/name
    condition,args=where(run_id,filters)
    count=0
    try:
        from contextlib import ExitStack
        with ExitStack() as stack:
            files={key:stack.enter_context((stage/(key+'.csv')).open('w',encoding='utf-8-sig',newline='')) for key in ['routes','legs','daily_daylight','rejections','fares']}
            writers={key:csv.writer(f) for key,f in files.items()}
            writers['routes'].writerow(['id','run_id','cities','nights','price','status','applicable_start_dates','comfort_deviation_minutes','intermediate_timetable_warning'])
            writers['legs'].writerow(['route_id','index','trip','train','from','to','departure_seconds','arrival_seconds','service_day_offset','sleeper','price'])
            writers['daily_daylight'].writerow(['route_id','day_index','on_train_seconds','off_train_seconds'])
            writers['rejections'].writerow(['reason','rejected_states','excluded_full_routes'])
            writers['fares'].writerow(['route_id','start_date','boarding_date','train','from','to','seat','berth','adult_reference','student_low','student_high','formula','source','observed_at','policy'])
            for reason,n in json.loads(run['audit']).get('rejection_states',{}).items():writers['rejections'].writerow([reason,n,'unknown (prefix count)'])
            candidates=stack.enter_context((stage/'candidates.jsonl').open('w',encoding='utf-8'))
            index=stack.enter_context((stage/'route-index.md').open('w',encoding='utf-8'))
            index.write('# 全部导出方案索引\n\n')
            with store.connect() as db:
                for row in db.execute('SELECT r.* FROM results r INDEXED BY results_run_stream WHERE '+condition+' ORDER BY r.rowid',args):
                    check_cancel(cancel)
                    if count%100==0:check_free(parent)
                    p=store.expand_payload(row['payload'],db)
                    assessment=db.execute('SELECT payload FROM fare_assessments WHERE result_id=?',(p['id'],)).fetchone()
                    if assessment:
                        stored=json.loads(assessment[0])
                        current=resolved_assessment(p,price_index,stored)
                        p['latest_fare_assessment']=current if stored.get('shared_cache') else stored
                        if not stored.get('shared_cache') and current!=stored:p['reused_fare_assessment']=current
                        for start,instance in current['dates'].items():
                            for leg,detail in zip(p['legs'],instance['legs']):
                                for fare in applicable_fares(detail.get('fares',[]),leg):
                                    student=fare.get('student') or {}
                                    writers['fares'].writerow([safe(v) for v in [p['id'],start,detail.get('boarding_date',''),leg['train'],leg['from'],leg['to'],fare['seat'],fare['berth'],fare['adult'],student.get('low'),student.get('high'),student.get('formula'),fare['source'],fare.get('observed_at'),student.get('policy')]])
                    else:
                        for variant in p.get('date_evidence_variants',[]):
                            for leg,detail in zip(p['legs'],variant['legs']):
                                for fare in detail.get('fares',[]):
                                    if fare['seat']=='硬座' or not fare_covers_leg(fare,leg):continue
                                    adult=fare['low'] if fare['low']==fare['high'] else f"{fare['low']}—{fare['high']}"
                                    student=fare['passenger']=='student'
                                    writers['fares'].writerow([safe(v) for v in [p['id'],variant['start_date'],detail['boarding_date'],leg['train'],leg['from'],leg['to'],fare['seat'],fare['berth'],None if student else adult,fare['low'] if student else None,fare['high'] if student else None,fare.get('formula'),fare['source'],fare.get('observed_at'),fare.get('policy')]])
                    candidates.write(encode(p)+'\n')
                    writers['routes'].writerow([safe(v) for v in [p['id'],run_id,' → '.join(p['pattern']),p['nights'],row['price'],row['status'],','.join(p['dates']),comfort_minutes(p,json.loads(run['request'])),'；'.join(timetable_warning(l) for l in p['legs'] if timetable_warning(l))]])
                    for i,l in enumerate(p['legs']):writers['legs'].writerow([safe(v) for v in [p['id'],i,l['trip'],l['train'],l['from'],l['to'],l['dep'],l['arr'],l['service_day_offset'],l['sleeper'],l['price']]])
                    request=json.loads(run['request']);length=(request['daylight_end']-request['daylight_start'])*60
                    for day,seconds in p['daylight_seconds'].items():writers['daily_daylight'].writerow([p['id'],day,seconds,length-seconds])
                    index.write(f"- {p['id']} · {' → '.join(p['pattern'])} · {p['status']} · {comfort_text(p,json.loads(run['request']))}\n")
                    count+=1
                    if count%1000==0:progress(stage='导出',current=count)
        (stage/'audit.json').write_text(encode(dict(run=run,snapshot=snap,scope=scope,filters=filters,exported_count=count)),encoding='utf-8')
        evidence=stage/'evidence';evidence.mkdir()
        (evidence/'task-frozen.json').write_text(encode(json.loads(run['checkpoint'])),encoding='utf-8')
        for record in store.rows('SELECT * FROM evidence WHERE snapshot=?',(snap['id'],)):
            (evidence/(record['id']+'.json')).write_text(encode(record),encoding='utf-8')
        audit=json.loads(run['audit'])
        quality=json.loads(snap['quality'])
        report_lines=[
            '# RailNomad 规划报告', '', f'导出范围：{scope}',
            f'任务：{run_id}', f'状态：{run["status"]}', f'本次导出具体方案：{count}',
            '首段实际登车日：'+(json.loads(run['request']).get('departure_date') or '全部适用日期'),
            daylight_rule_text(json.loads(run['request'])),
            time_windows_text(json.loads(run['request'])),
            f'快照：{snap["tag"]} / {snap["id"]}',
            '实际服务日历：'+' — '.join(quality['calendar']),
            f'隔离异常服务：{len(quality.get("quarantined_trips",[]))} 趟；未知城市归属：{quality["unknown_cities"]} 站',
            f'局部时刻异常服务：{len(quality.get("time_affected_trips",[]))} 趟；不可用于上下车的时刻：{quality.get("unusable_time_events",0)} 个；区间异常须查看逐段证据',
            '', '## 冻结参数', '', '```json',
            json.dumps(json.loads(run['request']),ensure_ascii=False,indent=2), '```',
            '', '## 完整性与证据', '',
            '本地枚举仅代表所声明快照、日历和用户约束；不证明全国运行图完整。',
            '学生资格和余票独立核实；参考预算合格不表示确定可买。',
            '未完成任务导出为部分结果；共享前缀排除次数不代表完整排除路线数量。',
            '', '```json', json.dumps(audit,ensure_ascii=False,indent=2), '```',
            '', '## 数据质量警告', '', *['- '+w for w in quality.get('warnings',[])],
            '', '## 索引及复现', '',
            '全部本次导出方案见 route-index.md。具体实例、日期化票价及证据见 candidates.jsonl。',
            'audit.json 保留原任务、快照质量、导出范围和筛选条件。evidence/task-frozen.json 是任务冻结证据及映射；其他 evidence 文件是导出时的来源档案。',
            'rejections.csv 的计数粒度为被排除搜索状态，未展开的完整路线数为未知。', ''
        ]
        mapping_provenance=json.loads(run['checkpoint']).get('city_mapping_provenance',{})
        source_lines=['','## 城市归属来源','']
        if mapping_provenance:
            source_lines.extend([mapping_provenance.get('attribution',''),
                                 '本任务冻结的归属来源：'+mapping_provenance['provider'],
                                 '地理编码依据：'+mapping_provenance.get('guideline',''),
                                 '覆盖范围：'+mapping_provenance.get('coverage','见冻结证据'),
                                 '仅导出车站标识与城市名称，不包含边界几何；未知归属保持未知。'])
        else:
            source_lines.append('使用任务原有冻结归属及来源，见 evidence/task-frozen.json；未将历史来源改标为 OSM。')
        report_lines.extend(source_lines)
        from .attribution import data_notices
        credits=data_notices(snap,json.loads(run['checkpoint']))
        report_lines.extend(['','## 运行图、坐标与归属署名','',credits,
                             '', '共享本目录中的 CSV / JSONL 时，请一并保留 DATA-LICENSES.txt 及来源记录。'])
        (stage/'DATA-LICENSES.txt').write_text(credits+'\n\n应用自有代码的 MIT 不覆盖上游数据的独立许可。\n',encoding='utf-8')
        if outdated_search_rules(json.loads(run['request'])):
            report_lines[1:1] = ['', '**本任务采用旧版检索规则。本导出保留原始历史结果，不表示已通过新版校验；请按当前规则新建检索。**', '']
        (stage/'report.md').write_text('\n'.join(report_lines),encoding='utf-8')
        check_cancel(cancel);check_free(parent);stage.rename(target)
        with store.connect() as db:db.execute('INSERT INTO exports(run_id,scope,path,created) VALUES(?,?,?,?)',(run_id,scope,str(target),now()))
        return str(target)
    finally:
        if stage.exists():shutil.rmtree(stage)
