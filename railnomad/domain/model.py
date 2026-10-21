from dataclasses import asdict, dataclass, field
import math
from datetime import date, datetime, timezone, timedelta


RULES_VERSION = "2026-10-19.11-endpoint-days"


@dataclass
class Request:
    origin: str
    destination: str
    origin_kind: str = 'station'
    destination_kind: str = 'station'
    min_nights: int = 3
    max_nights: int = 4
    budget: float = 1000
    consecutive: bool = True
    min_sleeper_minutes: int = 450
    target_depart_start: int = 1170
    target_depart_end: int = 30
    depart_start: int = 1140
    depart_end: int = 60
    target_arrive_start: int = 390
    target_arrive_end: int = 570
    arrive_start: int = 360
    arrive_end: int = 600
    daylight_start: int = 420
    daylight_end: int = 1200
    min_off_train: int = 600
    limit_daylight: bool = True
    max_daylight_on_train: int | None = 180
    first_daylight_limit: int | None = 240
    last_daylight_limit: int | None = 300
    same_station_gap: int = 600
    same_city_gap: int = 660
    short_same_station_gap: int = 30
    short_same_city_gap: int = 90
    allow_short_hsr: bool = False
    short_hsr_max: int = 60
    required_cities: list = field(default_factory=list)
    required_city_mode: str = 'AND'
    forbidden_cities: list = field(default_factory=list)
    preferred_cities: list = field(default_factory=list)
    preferred_stations: list = field(default_factory=list)
    allowed_stations: list = field(default_factory=list)
    other_preferences: str = ''
    departure_date: str | None = None
    rules_version: str = RULES_VERSION

    def validate(self):
        if self.departure_date is not None:
            try:
                selected = date.fromisoformat(self.departure_date)
            except (ValueError,TypeError):
                raise ValueError('出发日期须为 YYYY-MM-DD') from None
            if selected.isoformat() != self.departure_date:
                raise ValueError('出发日期须为 YYYY-MM-DD')
        integer_fields=('min_nights','max_nights','min_sleeper_minutes','target_depart_start','target_depart_end','depart_start','depart_end','target_arrive_start','target_arrive_end','arrive_start','arrive_end','daylight_start','daylight_end','min_off_train','same_station_gap','same_city_gap','short_same_station_gap','short_same_city_gap','short_hsr_max')
        if any(type(getattr(self,key)) is not int for key in integer_fields):
            raise ValueError('夜数和分钟参数须为整数')
        if isinstance(self.budget,bool) or not isinstance(self.budget,(int,float)) or not math.isfinite(self.budget):
            raise ValueError('预算须为有限的非负金额')
        if not self.origin or not self.destination or self.origin == self.destination:
            raise ValueError('请填写不同的出发地与目的地')
        if self.origin_kind not in ('station', 'city') or self.destination_kind not in ('station', 'city'):
            raise ValueError('地点类型无效')
        if not 1 <= self.min_nights <= self.max_nights:
            raise ValueError('夜数须为正整数，且最少不能大于最多')
        if self.budget < 0 or self.min_sleeper_minutes < 1 or self.short_hsr_max < 1:
            raise ValueError('预算及乘车时长无效')
        for key in ('target_depart_start', 'target_depart_end', 'depart_start', 'depart_end',
                    'target_arrive_start', 'target_arrive_end', 'arrive_start', 'arrive_end',
                    'daylight_start', 'daylight_end'):
            if not 0 <= getattr(self, key) < 1440:
                raise ValueError('时间须在 00:00—23:59')
        if not self.arrive_start <= self.target_arrive_start <= self.target_arrive_end <= self.arrive_end:
            raise ValueError('舒适到站时间须在极限到站范围内')
        if not window_contains(self.depart_start, self.depart_end, self.target_depart_start, self.target_depart_end):
            raise ValueError('舒适发车时间须在极限发车范围内（跨午夜按同一夜晚比较）')
        if not self.daylight_start < self.daylight_end:
            raise ValueError('白昼起点须早于终点')
        if type(self.limit_daylight) is not bool:
            raise ValueError('白昼在车限制开关须为布尔值')
        if self.max_daylight_on_train is not None and (type(self.max_daylight_on_train) is not int or self.max_daylight_on_train<0):
            raise ValueError('白昼在车上限须为非负整数分钟')
        if self.limit_daylight and not 0 <= daylight_limit_minutes(self) <= self.daylight_end-self.daylight_start:
            raise ValueError('白昼在车上限须在白昼窗口长度内')
        for value in (self.first_daylight_limit,self.last_daylight_limit):
            if value is not None and (type(value) is not int or not 0<=value<=self.daylight_end-self.daylight_start):
                raise ValueError('出发当天、抵达当天的白昼在车上限须为空（不限）或在白昼窗口长度内')
        if self.min_off_train<0:
            raise ValueError('最少离车分钟数须在白昼长度内')
        if min(self.same_station_gap, self.same_city_gap, self.short_same_station_gap, self.short_same_city_gap) < 0:
            raise ValueError('换乘间隔不能为负数')
        if self.required_city_mode not in ('AND','OR'):
            raise ValueError('必须停留城市关系须为 AND 或 OR')
        conflicts=set(self.required_cities) & set(self.forbidden_cities)
        if conflicts and (self.required_city_mode=='AND' or conflicts==set(self.required_cities)):
            raise ValueError('同一城市不能同时为必须和禁止')
        return self

    def to_dict(self):
        return asdict(self)


def today_china():
    return datetime.now(timezone(timedelta(hours=8))).date()


def in_wrapped_window(seconds, start, end):
    value = seconds % 86400
    return start*60 <= value <= end*60 if start <= end else value >= start*60 or value <= end*60


def short_window(dep, arr, request):
    start = request.target_arrive_start * 60
    end = request.target_depart_end * 60
    if end < start:
        end += 86400
    # Check one complete anchored window, including after-midnight travel.
    anchor = dep // 86400
    return any(d*86400 + start <= dep <= arr <= d*86400 + end for d in (anchor-1, anchor))


def daylight(legs, request):
    totals = {}
    for leg in legs:
        for day in range(leg['dep']//86400, leg['arr']//86400 + 1):
            overlap = max(0, min(leg['arr'], day*86400 + request.daylight_end*60) - max(leg['dep'], day*86400 + request.daylight_start*60))
            totals[day] = totals.get(day, 0) + overlap
    allowed=not legs or daylight_totals_allowed(totals,request,legs[0]['dep']//86400,legs[-1]['arr']//86400)
    return totals,allowed


def daylight_totals_allowed(totals,request,first_day,last_day,*,complete=True):
    if not request.limit_daylight:return True
    for day,seconds in totals.items():
        limits=[]
        if day==first_day:limits.append(request.first_daylight_limit)
        if complete and day==last_day:limits.append(request.last_daylight_limit)
        if day!=first_day and (day<last_day or complete and day!=last_day):limits.append(daylight_limit_minutes(request))
        # A prefix's current day may still become the final arrival day.
        # Defer that day's ordinary cap until it closes or the route ends.
        if any(value is not None and seconds>value*60 for value in limits):return False
    return True


def daylight_limit_minutes(request):
    return request.max_daylight_on_train if request.max_daylight_on_train is not None else request.daylight_end-request.daylight_start-request.min_off_train


def daylight_rule_text(parameters):
    if not parameters.get('limit_daylight',True):return '白昼在车不限制（仅统计）'
    start=parameters.get('daylight_start',420);end=parameters.get('daylight_end',1200)
    maximum=parameters.get('max_daylight_on_train')
    if maximum is None:maximum=end-start-parameters.get('min_off_train',600)
    if 'first_daylight_limit' not in parameters:
        return f'白昼 {start//60:02d}:{start%60:02d}—{end//60:02d}:{end%60:02d} 每天在车最多 {maximum} 分钟（历史任务，含首末段）'
    def describe(value):
        if value is None or value>=end-start:return '不限'
        hours,minutes=divmod(value,60)
        return (f'{hours} 小时 ' if hours else '')+(f'{minutes} 分钟' if minutes or not hours else '')
    if parameters.get('rules_version')!=RULES_VERSION:
        return (f'白天 {start//60:02d}:{start%60:02d}—{end//60:02d}:{end%60:02d}：历史口径，中途每天 {describe(maximum)}；'
                f'第一趟每天 {describe(parameters.get("first_daylight_limit"))}；最后一趟每天 {describe(parameters.get("last_daylight_limit"))}')
    return (f'白天 {start//60:02d}:{start%60:02d}—{end//60:02d}:{end%60:02d}：中间日期每天 {describe(maximum)}；'
            f'出发当天 {describe(parameters.get("first_daylight_limit"))}；抵达当天 {describe(parameters.get("last_daylight_limit"))}（按当天全部乘车合计）')


def nights_for(dep, arr):
    # A night is the overnight window ending at 06:00. Overnight boarding
    # up to 01:00 belongs to the preceding evening. Daylight-only legs count 0.
    return {d-1 for d in range(dep//86400, arr//86400+1)
            if max(dep, d*86400) < min(arr, d*86400+6*3600)}


def window_relaxation(seconds,start,end):
    if in_wrapped_window(seconds,start,end):return 0
    clock=seconds%86400
    return min(abs(clock-start*60),86400-abs(clock-start*60),abs(clock-end*60),86400-abs(clock-end*60))/60


def comfort_for_leg(leg,request):
    """Comfort is a recommendation score, including permitted endpoint exceptions."""
    if leg['short']:return dict(departure=0,arrival=0)
    if isinstance(request,dict):request=Request(**request)
    nights=nights_for(leg['dep'],leg['arr'])
    if not nights:return dict(departure=0,arrival=0)
    start=min(nights)*86400+request.target_depart_start*60
    end=min(nights)*86400+request.target_depart_end*60
    if request.target_depart_end<request.target_depart_start:end+=86400
    departure=max(start-leg['dep'],leg['dep']-end,0)/60
    clock=leg['arr']%86400
    arrival=max(request.target_arrive_start*60-clock,clock-request.target_arrive_end*60,0)/60
    return dict(departure=departure,arrival=arrival)


def comfort_minutes(payload,request):
    if isinstance(request,dict):request=Request(**request)
    return sum(sum(comfort_for_leg(leg,request).values()) for leg in payload['legs'])


def comfort_text(payload,request):
    value=comfort_minutes(payload,request)
    return '舒适时间内' if value==0 else f'超出舒适时间 · 合计偏离 {value:g} 分钟 · 推荐靠后'


def time_windows_text(parameters):
    def clock(key):
        value=parameters[key]
        return f'{value//60:02d}:{value%60:02d}'
    return (f'舒适：到站 {clock("target_arrive_start")}—{clock("target_arrive_end")}，发车 {clock("target_depart_start")}—{clock("target_depart_end")}；'
            f'极限：到站 {clock("arrive_start")}—{clock("arrive_end")}，发车 {clock("depart_start")}—{clock("depart_end")}（发车可跨午夜）')


def timetable_warning(leg):
    issues=leg.get('timetable_issues',[])
    unused=leg.get('unused_endpoint_time_issues',[])
    if not issues and not unused:return ''
    def clock(value):return f'{value//3600:02d}:{value//60%60:02d}:{value%60:02d}'
    details=[];seen=set()
    for issue in issues:
        key=issue['trip'],issue['seq'],issue['reason']
        if key in seen:continue
        seen.add(key)
        station=issue.get('stop_name',issue['stop'])
        if issue['reason']=='departure_before_arrival':
            arr,dep=(issue['value'],issue['related_value']) if issue['event']=='arr' else (issue['related_value'],issue['value'])
            details.append(f'{station}到达 {clock(arr)}、发车 {clock(dep)}')
        else:
            details.append(f'{station}{"到达" if issue["event"]=="arr" else "发车"} {clock(issue["value"])} 与站序 {issue["related_seq"]} 的 {clock(issue["related_value"])} 冲突')
    active='中间站时刻待核验（上下车时刻可用）：'+'；'.join(details) if issues else ''
    unneeded=[]
    for issue in unused:
        unneeded.append(f'{issue.get("stop_name",issue["stop"])}的{"到达" if issue["event"]=="arr" else "发车"}字段 {clock(issue["value"])} 有冲突，未用于本段计算')
    return '；'.join(v for v in (active,'上下车时刻可用；'+'；'.join(unneeded) if unused else '') if v)


def uncovered_nights(start, end, sleeper_nights, *, complete=True):
    """Uncovered nights across the whole trip, including before/after sleepers.

    While expanding a prefix, only a night ending at 06:00 is irreversible.
    A later sleeper can still cover a current night that has not ended yet.
    """
    missing = nights_for(start, end) - set(sleeper_nights)
    if complete:
        return missing
    return {night for night in missing if (night + 1) * 86400 + 6 * 3600 <= end}


def route_empty_nights(payload):
    legs = payload['legs']
    sleeper_nights = set()
    for leg in legs:
        if not leg['short']:
            sleeper_nights.update(nights_for(leg['dep'], leg['arr']))
    return sorted(uncovered_nights(legs[0]['dep'], legs[-1]['arr'], sleeper_nights))


def outdated_search_rules(request):
    return request.get('rules_version') != RULES_VERSION


def connection_gap(request, same_station, *, previous_short=False, next_short=False):
    if previous_short or next_short:
        return request.short_same_station_gap if same_station else request.short_same_city_gap
    return request.same_station_gap if same_station else request.same_city_gap


def arrival_allowed(seconds, request, *, terminal=False):
    clock = seconds % 86400
    if clock < request.arrive_start * 60:
        return False
    return terminal or clock <= request.arrive_end * 60


def window_contains(outer_start, outer_end, inner_start, inner_end):
    outer_end += 1440 if outer_end < outer_start else 0
    inner_end += 1440 if inner_end < inner_start else 0
    return any(outer_start <= inner_start + shift <= inner_end + shift <= outer_end
               for shift in (-1440, 0, 1440))


def departure_allowed(dep, arr, request, *, first=False):
    if not first:
        return in_wrapped_window(dep, request.depart_start, request.depart_end)
    nights = nights_for(dep, arr)
    if not nights:
        return False
    latest = min(nights) * 86400 + request.depart_end * 60
    if request.depart_end < request.depart_start:
        latest += 86400
    return dep <= latest


def train_night_text(leg):
    if leg['short']:return '短高铁 · 不计卧铺晚次'
    night=leg.get('train_night')
    if night is None:return '当前车次第几晚待核验'
    text=f'当前车次第 {night} 晚'+('（首晚）' if night==1 else '')
    covered=len(nights_for(leg['dep'],leg['arr']))
    return text+(f' · 本段跨 {covered} 晚' if covered>1 else '')


def train_night_for_leg(segment, full_calls, *, short=False):
    if short or segment.get('block'):
        # Same-vehicle block evidence need not identify the beginning of the
        # complete through train on this date. Do not invent a first night.
        return None
    if full_calls[0].get('departure_time_issues') or full_calls[-1].get('arrival_time_issues'):
        return None
    ridden = nights_for(segment['service_dep'], segment['service_arr'])
    complete = nights_for(full_calls[0]['dep'], full_calls[-1]['arr'])
    return 1 + min(ridden) - min(complete) if ridden and complete else None


def transport_category(kind):
    # Preserve rail subtypes; never use a train-number whitelist. Extended
    # codes follow the published GTFS HVT table, with unknown codes pending.
    if kind in (0,1,2,5,7,12,1400) or 100<=kind<=117 or 400<=kind<=405 or 900<=kind<=906:
        return 'rail'
    if kind in (3,4,6,11,800,1000,1100,1200,1702) or 200<=kind<=209 or 700<=kind<=716 or 1300<=kind<=1307 or 1500<=kind<=1507:
        return 'non_rail'
    return 'unknown'
