# RailNomad 公开发行版

夜车旅行路线规划：Windows 桌面版，以及在浏览器内计算的静态网页版。共用搜索规则，源码采用 MIT；第三方组件和数据分别按其许可提供。

本仓库是公开发行仓库，由开发侧受控导出。公开版不包含自动核价实现、私人票价缓存、用户任务或原始地图。它不要求登录，不提供购票或已部署的 API/MCP 服务。未知票价不算零元，预算仅在具有相应价格证据时起作用。

## 网页部署到 GitHub Pages

1. 将本仓库上传到你新建的 GitHub 仓库，默认分支命名为 `main`。
2. 仓库 Settings → Pages → Build and deployment，Source 选择 **GitHub Actions**。
3. Actions 中运行 **Deploy public web**；之后推送 main 中的更新会重新构建和部署。
4. 成功后的网址显示在该任务的部署环境中。项目路径下的 Worker、Wasm 和数据均使用相对地址，无须修改仓库名。

网页搜索在访客浏览器运行，首次加载约二十多 MB 静态资源；服务器不接收行程参数。刷新会清除会话结果。手机尚无真机性能承诺；复杂查询建议缩小范围。站点无自动票价查询，可前往官方页面自行核验。

本地预览（Python 3.12+、Node.js 22+）：

```sh
npm --prefix web ci
npm --prefix web run build
npm --prefix web start
```

打开 http://127.0.0.1:4173 。构建只读取 `public-data/`，不会扫描电脑上的桌面用户目录。可通过 `RAILNOMAD_PYTHON` 指定 Python 路径。

## Windows 桌面发行

现成包由 GitHub Releases 提供（维护者发布版本后可见）。必须保留完整程序目录及 `third-party-notices/`。首次启动自动安装随包运行图，默认数据目录为 `%LOCALAPPDATA%/RailNomadPublic/`，与本地完整版本隔离。需要自定义时设置 `RAILNOMAD_PUBLIC_HOME`。

重建时先安装 Python 3.12：

```powershell
python -m pip install -r requirements-dev.txt
python scripts/download_license_sources.py
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1 -Python python
```

`public-v*` 标签触发 Windows 打包和 Release 发布工作流。桌面包不包含 Web、网页运行时、用户数据库和原始地图；Qt/PySide 完整对应源码按 LGPL 配套放在桌面包内。源档案不进 Git，构建时按固定 SHA-256 下载。

## 数据、许可与版本管理

运行图及版本位于 `public-data/release.json`，来源是 wensimehrp/chinese-railway-gtfs，上游声明 CC BY 4.0，车站坐标归属 © OpenStreetMap contributors（ODbL）。本项目执行格式转换和质量处理；社区时刻、卧铺和票价缺口不能视为铁路官方确认。

城市名称参考表保留来源且核对站点身份，不随包提供边界地图。`LICENSE` 仅适用于自有源码；见 `THIRD_PARTY_NOTICES.md` 和 `web/THIRD_PARTY.md`。公开数据不附带抓取票价或私人证据。请勿将本项目解释为铁路官方产品或授权票务服务。

开发者从开发侧修改共用代码，再向新的暂存目录导出，与本仓库比较后提交；不要将完整开发目录或开发历史推送到这里。公开贡献先合回开发侧，再导出新版本，避免下一次导出覆盖修改。数据更新和程序版本分别记录：只改运行图也须重新构建并部署。

检查与测试：

```sh
python scripts/check_public_release.py
python -m pip install pytest==8.3.5
python -m pytest -q -p no:cacheprovider
```

发布前通过公开边界检查、共享核心测试和实际桌面/浏览器验收。工作流成功只代表在相应环境构建/部署成功，不代表获得未提供的数据许可或真实手机性能验收。
