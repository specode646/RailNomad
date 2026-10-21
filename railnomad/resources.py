"""Operational limits: preserve partial results rather than claiming exhaustion."""
import shutil,time
from pathlib import Path

GIB=1024**3
CATALOG_BYTES=8*GIB
MIN_FREE_BYTES=5*GIB


class ResourceLimit(Exception):
    pass


def limits(request):
    return dict(results=300_000,
                seconds=600 if request.allow_short_hsr else 1200)


def validate_request(request):
    if request.allow_short_hsr and request.short_hsr_max>60:raise ValueError('短高铁单段最长 60 分钟')


def risk_warnings(request):
    warnings=[]
    if request.allow_short_hsr:warnings.append('短高铁可能显著增加路线变体；建议先关闭短高铁，或限定站点、城市和夜数。')
    threshold=4 if request.allow_short_hsr else 6
    if request.max_nights>threshold:warnings.append(f"{'含短高铁超过 4 晚' if request.allow_short_hsr else '不含短高铁超过 6 晚'}，路线组合可能快速增长；建议减少夜数或限定城市、站点。")
    if not request.departure_date:warnings.append('全部适用日期会增加日历计算与证据体积；建议选定一个出发日期。')
    if not request.consecutive:warnings.append('允许空夜会增加日期组合；建议优先连续卧铺夜。')
    return warnings


def description(request):
    cap=limits(request)
    text=f"最多保存 {cap['results']:,} 条方案，每次运行最多 {cap['seconds']//60} 分钟；触顶保存部分结果，明确标为不完整。"
    warnings=risk_warnings(request)
    if warnings:text='风险提示：\n'+'\n'.join(warnings)+'\n\n'+text
    return text+'\n目录库及日志合计上限 8 GiB，磁盘至少保留 5 GiB。'


class SearchGuard:
    def __init__(self,store,request):
        self.store=store;self.cap=limits(request);self.started=time.monotonic();self.next_check=0

    def check(self,*,force=False):
        current=time.monotonic()
        if current-self.started>=self.cap['seconds']:
            raise ResourceLimit(f"达到本次运行 {self.cap['seconds']//60} 分钟上限；可继续检索，或缩小范围新建任务")
        if not force and current<self.next_check:return
        self.next_check=current+0.25
        paths=[Path(str(self.store.path)+suffix) for suffix in ('','-wal','-shm','-journal')]
        size=0
        for path in paths:
            try:size+=path.stat().st_size
            except FileNotFoundError:pass
        if size>=CATALOG_BYTES:raise ResourceLimit('目录库及日志达到 8 GiB 上限；请先删除不需要的任务，再继续')
        check_free(self.store.home)

    def check_count(self,count):
        if count>=self.cap['results']:
            raise ResourceLimit(f"达到本任务 {self.cap['results']:,} 条方案上限；请缩小日期、夜数或关闭短高铁后新建任务")


def check_free(path):
    if shutil.disk_usage(path).free<MIN_FREE_BYTES:raise ResourceLimit('磁盘剩余不足 5 GiB，请先清理空间')
