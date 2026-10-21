# 网页许可与数据来源

RailNomad 自有 HTML、JavaScript、Python 和 SVG 按 MIT。网页不加载 Qt/PySide。Pyodide 314.0.7 按 MPL 2.0，保持发行文件未修改；其完整对应源代码可在 https://github.com/pyodide/pyodide/tree/314.0.7 取得。其 CPython 3.14.2 及所含组件分别适用原始许可，见站点 licenses/ 中的完整声明。MPL 源码获取权不会因本项目 MIT 声明而受到限制。

站点 licenses/ 保留 MPL、Pyodide 原版权、CPython 历史及第三方许可、CC BY 4.0、ODbL 与站城发布者声明。网页运行时源代码入口和构建说明见 https://pyodide.org/en/314.0.7/development/building-from-sources.html 。没有修改运行时，也不以 MIT 重新许可 Wasm/标准库。

该版本使用 Emscripten 5.0.3 工具链；其 MIT / Illinois 声明、musl 的原版权，以及 compiler-rt / libunwind 的 LLVM 许可和例外也保存在 licenses/，未统一改为 MPL。精确来源、版本及许可材料指纹见 licenses/RUNTIME-SOURCES.json。

运行图作者 wensimehrp，项目 chinese-railway-gtfs：https://github.com/wensimehrp/chinese-railway-gtfs ，上游声明 CC BY 4.0。具体附件、指纹、有效日历在 data/input.json 的 snapshot 中；本项目进行了 SQLite 转换、质量检查和已披露的时刻处理。坐标：© OpenStreetMap contributors，https://www.openstreetmap.org/copyright ，ODbL 1.0。

站城事实表参考 cn-atlas / Amll（发布者声明 ISC）和 GeoCode-Release / zzhonglei（发布者声明 MIT），保留表内来源。不提供原始边界，也不替其重新授予许可。无私人任务、票价缓存或自动核价响应。软件及其估算不代表余票、学生优惠资格或可购票保证。
