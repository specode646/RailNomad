# 人工证据与城市映射

数据中心支持 CSV 城市映射：`stop_id,city,source`。ID 必须存在于所选快照，source 必填（原始文件或可核对链接）。不凭站名猜城市；映射有独立版本来源，任务冻结映射。未映射站点按站点块展示并标明城市约束待核验。

数据中心支持 JSON 数组证据。每条都需要 `kind,source,observed_at,date,trip,from,to`。date 是实际登车日，observed_at 是带时区的采集时间；trip/from/to 使用 GTFS ID。人工证据不会自动表示官方证据。

票价另需 `seat,berth,passenger,low,high`，passenger 为 adult 或 student。所有日期、席别、铺位变体完整保留；只有学生报价全部覆盖且上界不超预算才判预算通过。无学生价时不凭成人价统一打折。估算学生价还必须提供 `estimated:true,formula,rule_version,conditions,rounding`。本版本校验并保存外部给出的估算及规则，不自行解释或执行公式。

卧铺和物理区段证据分别使用 `kind:sleeper`、`kind:track` 及 `status:passed/failed/pending`。source 应具体说明核验依据；不能将站序代理当作物理区段已核验。

格式示例（**仅模拟，不能用于真实服务核验**）：

```json
[
  {"kind":"fare","source":"simulated fixture","observed_at":"2026-10-18T12:00:00+08:00","date":"2026-12-31","trip":"t1","from":"a","to":"b","seat":"硬卧","berth":"上铺","passenger":"student","low":100,"high":100}
]
```

任务冻结导入时的证据。更改证据后新建任务，避免改写历史结果。余票和学生资格始终独立标示未核实。上游 GTFS 不含票价，本版不承诺可用的实时票价接口，不抓取需要登录或验证码的服务。

卧铺预算只使用明确的卧铺席别（硬卧、软卧、高级软卧、动卧、一/二等卧）或 `seat_kind:"sleeper"` 证据；已知硬座/无座等座席价不能替代卧铺价。未知席别保留原证据但不代入卧铺预算。金额必须有限且非负，合计采用十进制比较，不擅自舍入；跨预算区间保留待核验。


0.3.0 更新：用户已授权按折扣估算学生参考价并使用预算。现在也支持行程详情或当前页后台查询公开成人价，按现行规则派生学生参考区间；规则、缺公布价时的区间和独立价格评价见 [ADR-0009](adr/0009-fares-city-mode-and-visuals.md)。原文关于本版仅人工入口的描述为历史状态。已有任务冻结数据不改写，更新报价另存并随导出附带，新任务可冻结同日已查证据。
