# 第三方运行库与数据

本公开发行仓库的自有代码和自绘 SVG 按 MIT；第三方库与数据保留独立许可。发布时不得删除以下配套或将其统一解释为 MIT。

| 对象 | 许可与交付 |
| --- | --- |
| Qt Core、Gui、Widgets、SVG 6.8.3；PySide6 Essentials / Shiboken 6.8.3 | LGPL v3；© The Qt Company Ltd 及原贡献者。完整对应源码、LGPL/GPL 正文、上游版权与替换重建说明随 Windows 包提供；允许修改、替换库并为调试修改进行必要逆向工程 |
| CPython 3.12（桌面）/ 3.14.2（Web） | PSF、历史许可及其中各第三方组件原条款；具体构建版本和版权保存在对应发行配套中 |
| OpenSSL、libffi、SQLite、Microsoft 运行库 | 原始独立条款；OpenSSL Apache 2.0，libffi 原许可，SQLite 公有领域声明，Microsoft 条件只约束其运行库；保留原二进制版权。实际桌面文件、版本和指纹见 third-party-notices/manifest.json |
| PyInstaller 启动器及运行时钩子 | GPL 2 或以后版本及 Bootloader Exception；运行时钩子 Apache 2.0。例外允许生成程序按应用自己的许可证分发 |
| Pyodide 314.0.7（仅 Web） | MPL 2.0；未修改原运行时文件。完整对应源码与构建说明：https://github.com/pyodide/pyodide/tree/314.0.7 。站点 licenses/ 保留许可证及 CPython/其他组件声明 |

运行图作者 wensimehrp，来源 https://github.com/wensimehrp/chinese-railway-gtfs ，上游声明 CC BY 4.0。精确附件 URL、来源指纹与有效日历见 public-data/release.json。RailNomad 将其转换成 SQLite，并执行质量检查和已披露的时刻处理；不表示原作者或铁路官方确认。车站坐标来自 © OpenStreetMap contributors，https://www.openstreetmap.org/copyright ，ODbL 1.0。

站城参考表记录站号、城市名、来源及站点身份指纹，参考 cn-atlas / Amll（发布者声明 ISC）和 GeoCode-Release / zzhonglei（发布者声明 MIT），保留原来源及发布者许可。公开交付不提供原始边界、多边形、底图或瓦片，不替原地图重新授予许可。已有用户数据按其实际来源处理；未知来源不推定开放。

本公开版没有 12306 自动核价实现、抓取响应或报价缓存，也没有私人任务和原始地图。手工导入与导出记录须具有相应使用权，并保留来源；实际票价、优惠资格及余票请在官方渠道自行核验。本项目不是铁路官方或授权票务服务。

Windows 分发须保留完整 third-party-notices/；Web 分发须保留 THIRD_PARTY.md、CODE-LICENSE.txt 和 licenses/。公开数据与原始归属记录不随应用 MIT 改换许可。
