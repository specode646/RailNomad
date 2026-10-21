# RailNomad

夜间乘车，白天游览。RailNomad 帮你寻找连接多个城市的夜车路线，按到发时间、停留城市和白天乘车时长安排旅程。

**[在线使用](https://specode646.github.io/RailNomad/)** · **[下载 Windows 版](https://github.com/specode646/RailNomad/releases/latest)**

## 能做什么

- 按城市或车站、出发日期和夜数搜索路线。
- 设置舒适与极限到发时间、白天乘车上限，以及短高铁衔接。
- 指定必须停留或避开的城市，查看不同车次与换站搭配。
- 通过时间轴查看行程、乘车日期和中途停留。
- Windows 版还支持保存任务、收藏、路线比较和导出。

## 开始使用

### 网页版

打开 [RailNomad 网页版](https://specode646.github.io/RailNomad/)，选择出发地、目的地、日期和夜数，点击“搜索路线”。展开结果即可查看车次和完整行程。

无需安装或登录，搜索在你的浏览器中完成。首次使用会下载约 25 MB 的运行环境与数据，加载速度取决于网络；刷新或关闭页面会清除本次结果。

### Windows 版

1. 在 [Releases](https://github.com/specode646/RailNomad/releases/latest) 下载 `RailNomad-public-windows-x64.zip`。
2. 解压整个压缩包，运行其中的 `RailNomad.exe`，无需安装 Python。
3. 在“新建规划”中设置行程并开始搜索。

程序已附带一份运行图。使用时请保留完整程序目录；任务、收藏等数据保存在 `%LOCALAPPDATA%/RailNomadPublic/`。

## 使用须知

搜索结果是基于社区运行图生成的候选路线。实际开行、卧铺席别、票价与余票，请在 [12306](https://www.12306.cn/) 核实。本项目不提供购票服务。

当前版本不自动查询票价。Windows 版可[导入有来源的票价资料](docs/manual-evidence.md)，用于参考费用和预算计算；缺失的价格保持未知。

复杂路线可能产生大量结果，建议先从较少夜数开始。运行图只覆盖一定日期范围，数据过期后需要更新。手机可使用网页，实际性能取决于设备，尚未完成真机性能验收。

## 开发

本地运行、测试、Windows 打包和 GitHub Pages 部署，见 [开发与部署说明](docs/DEVELOPMENT.md)。

欢迎通过 [Issues](https://github.com/specode646/RailNomad/issues) 反馈问题。涉及搜索结果时，请附上起终点、出发日期、夜数和相关设置。

## 许可证与数据来源

自有代码采用 [MIT License](LICENSE)。运行图来自 [wensimehrp/chinese-railway-gtfs](https://github.com/wensimehrp/chinese-railway-gtfs)，上游声明为 CC BY 4.0；车站坐标归属 [© OpenStreetMap contributors](https://www.openstreetmap.org/copyright)，采用 ODbL。

第三方组件、数据来源及各自许可见 [第三方声明](THIRD_PARTY_NOTICES.md) 和 [网页组件说明](web/THIRD_PARTY.md)。
