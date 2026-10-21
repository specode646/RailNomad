"""Global SQL filtering, stable ordering, paging; no page-local filtering."""
import json

FIRST_SLEEPER_NIGHT = "(SELECT json_extract(l.value,'$.train_night') FROM json_each(rn_payload(r.payload),'$.legs') l WHERE json_extract(l.value,'$.short')=0 ORDER BY CAST(l.key AS INTEGER) LIMIT 1)"


def where(run_id, filters=None, *, assessments=True):
    f = filters or {}
    clauses = ['r.run_id=?']
    args = [run_id]
    for key, op in [('min_nights','>='), ('max_nights','<=')]:
        if f.get(key) is not None:
            clauses.append('r.nights '+op+' ?')
            args.append(f[key])
    if f.get('budget') is not None:
        frozen="CASE WHEN r.price IS NULL OR r.price<=? THEN 1 ELSE EXISTS(SELECT 1 FROM json_each(rn_payload(r.payload),'$.date_evidence_variants') v WHERE json_extract(v.value,'$.price_low')<=?) END"
        if assessments:
            clauses.append('CASE WHEN EXISTS(SELECT 1 FROM fare_assessments WHERE result_id=r.id) THEN (SELECT low FROM fare_assessments WHERE result_id=r.id)<=? ELSE '+frozen+' END')
            args.append(f['budget'])
        else:
            clauses.append(frozen)
        args.extend([f['budget'],f['budget']])
    if f.get('known_price'):
        clauses.append('(r.price IS NOT NULL OR EXISTS(SELECT 1 FROM fare_assessments WHERE result_id=r.id AND high IS NOT NULL))' if assessments else 'r.price IS NOT NULL')
    if f.get('max_changes') is not None:
        clauses.append('r.changes<=?')
        args.append(f['max_changes'])
    if f.get('short_hsr') is not None:
        clauses.append('r.short_hsr=?')
        args.append(f['short_hsr'])
    if f.get('favorite'):
        clauses.append('EXISTS(SELECT 1 FROM favorites fav WHERE fav.result_id=r.id)')
    if f.get('status'):
        clauses.append('r.status=?')
        args.append(f['status'])
    if f.get('pattern'):
        clauses.append('r.pattern=?')
        args.append(f['pattern'])
    if 'ids' in f:
        clauses.append('r.id IN (' + ','.join('?' for _ in f['ids']) + ')' if f['ids'] else '0')
        args.extend(f['ids'])
    contains = []
    for city in f.get('cities', []):
        contains.append('EXISTS(SELECT 1 FROM json_each(r.pattern) c WHERE c.value=?)')
        args.append(city)
    if contains:
        clauses.append('(' + (' OR ' if f.get('city_mode') == 'OR' else ' AND ').join(contains) + ')')
    for city in f.get('exclude_cities', []):
        clauses.append('NOT EXISTS(SELECT 1 FROM json_each(r.pattern) c WHERE c.value=?)')
        args.append(city)
    for i,city in enumerate(f.get('ordered_cities', [])):
        clauses.append('EXISTS(SELECT 1 FROM json_each(r.pattern) c WHERE c.value=?)')
        args.append(city)
        if i:
            clauses.append('(SELECT key FROM json_each(r.pattern) WHERE value=? LIMIT 1) < (SELECT key FROM json_each(r.pattern) WHERE value=? LIMIT 1)')
            args.extend([f['ordered_cities'][i-1],city])
    for field in ('origin','destination'):
        if f.get(field):
            path = '$.legs[0].from' if field == 'origin' else '$.legs[#-1].to'
            values=[f[field]] if isinstance(f[field],str) else list(dict.fromkeys(f[field]))
            clauses.append('json_extract(rn_payload(r.payload),?) IN ('+','.join('?' for _ in values)+')')
            args.extend([path,*values])
    train_match = """EXISTS(SELECT 1 FROM json_each(rn_payload(r.payload),'$.legs') l
        WHERE json_extract(l.value,'$.trip')=wanted.value OR json_extract(l.value,'$.train')=wanted.value
        OR EXISTS(SELECT 1 FROM json_each(l.value,'$.through_trips') t WHERE t.value=wanted.value)
        OR instr('/'||json_extract(l.value,'$.train')||'/', '/'||wanted.value||'/')>0)"""
    # One JSON parameter avoids expression-depth and parameter-count failures
    # when the checkbox picker selects many train numbers.
    if f.get('trains'):
        clauses.append('NOT EXISTS(SELECT 1 FROM json_each(?) wanted WHERE NOT '+train_match+')')
        args.append(json.dumps(f['trains']))
    if f.get('exclude_trains'):
        clauses.append('NOT EXISTS(SELECT 1 FROM json_each(?) wanted WHERE '+train_match+')')
        args.append(json.dumps(f['exclude_trains']))
    if f.get('empty_nights') is not None:
        clauses.append("json_array_length(rn_payload(r.payload),'$.empty_nights')" + ('>0' if f['empty_nights'] else '=0'))
    if f.get('target_only'):
        clauses.append("COALESCE(json_extract(rn_payload(r.payload),'$.relaxation_minutes'),0)=0")
    if f.get('comfort_only'):
        clauses.append("COALESCE(json_extract(rn_payload(r.payload),'$.comfort_minutes'),json_extract(rn_payload(r.payload),'$.relaxation_minutes'),0)=0")
    if f.get('first_night') is not None:
        clauses.append(FIRST_SLEEPER_NIGHT+"=?")
        args.append(f['first_night'])
    for key,op in [('leg_min_minutes','<'),('leg_max_minutes','>')]:
        if f.get(key) is not None:
            clauses.append("NOT EXISTS(SELECT 1 FROM json_each(rn_payload(r.payload),'$.legs') l WHERE json_extract(l.value,'$.arr')-json_extract(l.value,'$.dep')"+op+"?)")
            args.append(f[key]*60)
    if f.get('sleeper'):
        clauses.append("EXISTS(SELECT 1 FROM json_each(rn_payload(r.payload),'$.date_evidence_variants') v,json_each(v.value,'$.legs') l,json_each(l.value,'$.fares') e WHERE json_extract(e.value,'$.seat')=?)")
        args.append(f['sleeper'])
    if f.get('min_off_train') is not None:
        clauses.append("NOT EXISTS(SELECT 1 FROM json_each(rn_payload(r.payload),'$.daylight_seconds') d WHERE d.value>?)")
        args.append((f.get('daylight_minutes',780)-f['min_off_train'])*60)
    return ' AND '.join(clauses), args


def query(store, run_id, filters=None, page=0, page_size=30, grouped=False, sort='changes'):
    validate_filters(store,run_id,filters)
    has_assessments=bool(store.rows('SELECT 1 FROM fare_assessments q CROSS JOIN results r ON r.id=q.result_id WHERE r.run_id=? LIMIT 1',(run_id,)))
    condition,args = where(run_id, filters,assessments=has_assessments)
    price='COALESCE((SELECT high FROM fare_assessments WHERE result_id=r.id),r.price)' if has_assessments else 'r.price'
    order = {'changes':'r.changes,r.id', 'price':'r.price IS NULL,r.price,r.id',
             'duration':"json_extract(rn_payload(r.payload),'$.legs[#-1].arr')-json_extract(rn_payload(r.payload),'$.legs[0].dep'),r.id",
             'nights':'r.nights,r.id',
             'daylight':"json_extract(rn_payload(r.payload),'$.min_daylight_off_minutes') DESC,r.id",
             'relaxation':"json_extract(rn_payload(r.payload),'$.relaxation_minutes'),r.id",
             'comfort':"COALESCE(json_extract(rn_payload(r.payload),'$.comfort_minutes'),json_extract(rn_payload(r.payload),'$.relaxation_minutes'),0),r.changes,r.id",
             'first_night':FIRST_SLEEPER_NIGHT+' IS NULL,'+FIRST_SLEEPER_NIGHT+',r.id',
             'evidence':"json_array_length(rn_payload(r.payload),'$.missing_evidence_keys'),r.id",
             'preference':"json_extract(rn_payload(r.payload),'$.preference_score') DESC,r.id"}.get(sort,'r.id')
    if has_assessments:order=order.replace('r.price',price)
    hint='' if 'ids' in (filters or {}) else ' INDEXED BY '+('results_run_browser' if grouped or (filters or {}).get('pattern') else 'results_run_page')
    with store.connect() as db:
        db.execute('BEGIN')
        if grouped:
            scalar={
                'duration':"json_extract(rn_payload(r.payload),'$.duration_seconds')",
                'daylight':"json_extract(rn_payload(r.payload),'$.min_daylight_off_minutes')",
                'relaxation':"json_extract(rn_payload(r.payload),'$.relaxation_minutes')",
                'comfort':"COALESCE(json_extract(rn_payload(r.payload),'$.comfort_minutes'),json_extract(rn_payload(r.payload),'$.relaxation_minutes'),0)",
                'first_night':FIRST_SLEEPER_NIGHT,
                'evidence':"json_array_length(rn_payload(r.payload),'$.missing_evidence_keys')",
                'preference':"json_extract(rn_payload(r.payload),'$.preference_score')"
            }.get(sort,'NULL')
            group_order={
                'changes':'MIN(r.changes),r.pattern',
                'price':'MIN(r.price) IS NULL,MIN(r.price),r.pattern',
                'duration':f'MIN({scalar}),r.pattern',
                'nights':'MIN(r.nights),r.pattern',
                'daylight':f'MAX({scalar}) DESC,r.pattern',
                'relaxation':f'MIN({scalar}),r.pattern',
                'comfort':f'MIN({scalar}),MIN(r.changes),r.pattern',
                'first_night':f'MIN({scalar}) IS NULL,MIN({scalar}),r.pattern',
                'evidence':f'MIN({scalar}),r.pattern',
                'preference':f'MAX({scalar}) DESC,r.pattern'
            }.get(sort,'r.pattern')
            if has_assessments:group_order=group_order.replace('r.price',price)
            sql=f"""SELECT r.pattern,COUNT(*) variants,
                       SUM(r.status='passed') passed_count,
                       SUM(r.status='pending') pending_count,
                       SUM(r.status='failed') failed_count,
                       SUM({price} IS NULL) unpriced_count,
                       MIN(r.nights) min_nights,MAX(r.nights) max_nights,
                       MIN(r.changes) min_changes,MAX(r.changes) max_changes,
                       MIN({price}) price,
                       COUNT(*) OVER() group_total,SUM(COUNT(*)) OVER() variant_total
                    FROM results r{hint} WHERE {condition}
                    GROUP BY r.pattern ORDER BY {group_order} LIMIT ? OFFSET ?"""
            rows=[dict(r) for r in db.execute(sql,args+[page_size,page*page_size])]
            if rows:
                groups,total=rows[0]['group_total'],rows[0]['variant_total']
                for row in rows:
                    row.pop('group_total');row.pop('variant_total')
                    selected=db.execute('SELECT r.id FROM results r INDEXED BY results_run_browser WHERE '+condition+' AND r.pattern=? ORDER BY '+order+' LIMIT 1',args+[row['pattern']]).fetchone()
                    row['representative']=selected[0]
            elif page or page_size==0:
                total,groups=db.execute('SELECT COUNT(*),COUNT(DISTINCT pattern) FROM results r'+hint+' WHERE '+condition,args).fetchone()
            else:
                groups,total=0,0
            return rows,groups,total
        total=db.execute('SELECT COUNT(*) FROM results r'+hint+' WHERE '+condition,args).fetchone()[0]
        # Sort identifiers and scalar keys, then fetch only the requested page.
        # Carrying full JSON through SQLite's sorter spills gigabytes to disk.
        ids=[r[0] for r in db.execute('SELECT r.id FROM results r'+hint+' WHERE '+condition+' ORDER BY '+order+' LIMIT ? OFFSET ?',args+[page_size,page*page_size])]
        rows={}
        for start in range(0,len(ids),500):
            chunk=ids[start:start+500]
            rows.update((r['id'],store.expand_row(r,db)) for r in db.execute('SELECT * FROM results WHERE id IN ('+','.join('?' for _ in chunk)+')',chunk))
        if has_assessments:
            for identity in ids:
                quote=db.execute('SELECT high FROM fare_assessments WHERE result_id=?',(identity,)).fetchone()
                if quote and quote[0] is not None:rows[identity]['price']=quote[0]
        return [rows[identity] for identity in ids],total,total


def validate_filters(store, run_id, filters=None):
    f=filters or {}
    for key in ('origin','destination'):
        value=f.get(key)
        if value is not None and (not isinstance(value,(str,list,tuple)) or isinstance(value,(list,tuple)) and any(not isinstance(v,str) or not v for v in value)):
            raise ValueError('筛选车站须为站点代码或多个站点代码')
    run=store.rows('SELECT request FROM runs WHERE id=?',(run_id,))
    if not run:raise ValueError('检索任务不存在')
    request=json.loads(run[0]['request'])
    lower=f.get('min_nights') if f.get('min_nights') is not None else request['min_nights']
    upper=f.get('max_nights') if f.get('max_nights') is not None else request['max_nights']
    budget=f.get('budget') if f.get('budget') is not None else request['budget']
    import math
    if type(lower) is not int or type(upper) is not int or isinstance(budget,bool) or not isinstance(budget,(int,float)) or not math.isfinite(budget) or budget<0:
        raise ValueError('筛选夜数须为整数，预算须为有限的非负金额')
    if lower < request['min_nights'] or upper > request['max_nights'] or budget > request['budget']:
        raise ValueError('此范围未计算，请新建检索任务，不能从当前目录推断没有路线')
    if lower>upper:
        raise ValueError('筛选最少夜数不能大于最多夜数')
