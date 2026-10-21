"""Human-readable credits for the snapshot actually used by a task."""
import json

GTFS_PROJECT = 'https://github.com/wensimehrp/chinese-railway-gtfs'
CC_BY = 'https://creativecommons.org/licenses/by/4.0/'
OSM_COPYRIGHT = 'https://www.openstreetmap.org/copyright'


def data_notices(snapshot, checkpoint=None):
    metadata = snapshot.get('metadata', {})
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    community = metadata.get('source') == 'community_gtfs' or metadata.get('repo') == 'wensimehrp/chinese-railway-gtfs'
    lines = ['运行图版本：' + str(snapshot.get('tag', '未提供'))]
    if community:
        lines.extend(['运行图作者/项目：wensimehrp / chinese-railway-gtfs · ' + GTFS_PROJECT,
                      '运行图许可：CC BY 4.0（上游声明）· ' + CC_BY,
                      '车站坐标上游：© OpenStreetMap contributors · ' + OSM_COPYRIGHT,
                      '坐标数据库许可：ODbL 1.0 · https://opendatacommons.org/licenses/odbl/1-0/'])
    else:
        lines.extend(['运行图来源：' + str(metadata.get('url') or metadata.get('source') or '用户导入，未提供'),
                      '运行图许可：' + str(metadata.get('license') or '用户未提供，不能推定为 MIT')])
    if metadata.get('url'):
        lines.append('原始附件：' + metadata['url'])
    lines.append('本应用的处理：GTFS 转换为 SQLite、时刻质量检查及已披露的午夜时刻修复；非原作者核验。')
    if checkpoint is not None:
        sources = json.dumps(checkpoint.get('city_mapping_sources', []), ensure_ascii=False)
        provenance = checkpoint.get('city_mapping_provenance', {})
        if provenance.get('attribution'):
            lines.append('冻结城市归属：' + provenance['attribution'])
        if 'BarbarossaWang/cn-atlas' in sources:
            lines.append('城市归属计算来源：cn-atlas / Amll（发布者声明 ISC）· https://github.com/BarbarossaWang/cn-atlas')
        if 'zzhonglei/GeoCode-Release' in sources:
            lines.append('城市归属交叉核对来源：GeoCode-Release / zzhonglei（边界包声明 MIT）· https://github.com/zzhonglei/GeoCode-Release')
        lines.append('站城表仅含站点标识、城市名称及来源记录；不包含原始边界几何，不对事实标签主张排他版权。')
    return '\n'.join(lines)
