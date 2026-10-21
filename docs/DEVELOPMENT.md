# 开发与部署

## 本地运行网页

需要 Python 3.12+、Node.js 22+。在仓库根目录执行：

```sh
npm --prefix web ci
npm --prefix web run build
npm --prefix web start
```

打开 http://127.0.0.1:4173 。请通过 HTTP 服务访问，直接打开 HTML 文件无法正常加载 Worker。

构建从 `public-data/` 读取运行图，生成 `web/dist/`，不读取桌面用户目录。可通过环境变量 `RAILNOMAD_PYTHON` 指定 Python 的可执行文件路径。网页使用 Pyodide Worker 运行共用搜索核心；运行环境、数据和代码均随静态站点提供。

## 测试

```sh
python -m pip install -r requirements-dev.txt
python scripts/check_public_release.py
python -m pytest -q -p no:cacheprovider
```

Linux 上部分测试会导入 Qt 组件，需要安装 `libegl1`、`libopengl0` 和 `libgl1`；无显示环境可设置 `QT_QPA_PLATFORM=offscreen`。

网页真实浏览器测试：

```sh
npm --prefix web ci
npm --prefix web run build
npm --prefix web test
```

Windows 默认使用本机 Edge；其他环境可通过 `BROWSER_EXECUTABLE` 指定 Chromium 系浏览器，或在 `web/` 下运行 `npx playwright install --with-deps chromium` 安装测试浏览器。设置 `PUBLIC_SITE_URL` 可验证已经部署的网站。

测试覆盖真实搜索、详情和时间轴、项目子路径、窄屏布局，以及没有业务后端请求的边界。窄屏布局检查不等于手机真机性能测试。

## Windows 打包

需要 Windows x64 和 Python 3.12。在仓库根目录执行：

```powershell
python -m pip install -r requirements-dev.txt
python scripts/download_license_sources.py
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1 -Python python
```

输出目录为 `dist/public/RailNomad/`。程序、动态库、运行图和 `third-party-notices/` 必须一并分发。构建会按固定 SHA-256 准备 Qt/PySide 对应源码，裁剪未使用模块，并收集许可和重建配套。

实际程序验收：

```powershell
python scripts/verify_public_desktop.py
```

该脚本使用临时数据目录，启动实际 EXE，执行搜索、导出及自动核价禁用检查。也可通过 `--exe` 指定待测程序。

桌面数据默认位于 `%LOCALAPPDATA%/RailNomadPublic/`；需要自定义时设置 `RAILNOMAD_PUBLIC_HOME`。

## GitHub Pages

1. 仓库默认分支使用 `main`。
2. 在 Settings → Pages → Build and deployment 中，将 Source 设为 **GitHub Actions**。
3. 推送 `main` 或手动运行 **Deploy public web**。

工作流会执行测试、构建 `web/dist/`、部署 Pages，并用真实浏览器访问部署后的 URL。资源使用相对地址，支持 `/仓库名/` 项目路径。Pages 只托管静态文件，搜索在浏览器中运行。

## 发布桌面版本

推送 `public-v*` 标签会触发 **Public Windows release**，构建压缩包及校验文件并发布到 GitHub Releases。手动运行该工作流只上传构建产物。

**Verify published Windows binary** 可下载已发布的压缩包，校验 SHA-256 后，在隔离 Windows 环境中运行实际程序。

## 数据和源码维护

- `PUBLIC-RELEASE.json` 记录程序发行版本；`public-data/release.json` 记录运行图来源、版本和指纹。
- 更新运行图后需要重新构建网页和桌面包；不直接使用个人目录库、任务、报价缓存或原始地图作为公开数据。
- 本仓库由主开发树生成。涉及共用源码的贡献，维护者需同步回主开发树，再生成后续公开版本，避免改动被覆盖。
- `node_modules/`、`dist/`、构建缓存和可重新获取的源码档案不提交到 Git。第三方许可与分发配套须保留，详见仓库内的许可声明。
