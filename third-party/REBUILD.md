# 替换 LGPL 动态库与重建

本包使用 Qt、PySide6 Essentials 和 Shiboken 的 LGPL v3 选项。LGPL 与其引用的 GPL 正文保存在 `licenses/upstream/` 中；第三方版权与许可条件仍适用。应用本身采用 MIT，不禁止用户调试、修改或替换这些库。

## 对应源码

`sources/` 附带 Qt Base 6.8.3、Qt SVG 6.8.3、PySide Setup 6.8.3 的完整上游源档案。`source-manifest.json` 保存原始地址、实际下载镜像、大小和 SHA-256。原档案包含库源码及其构建脚本；项目未修改这些库。

本运行包使用 Windows x64、MSVC 2022、共享动态库、Release 配置及 PySide 6.8.3 的稳定 Python ABI。实际二进制哈希及 Python/Qt 构建信息见 `manifest.json`。应用源代码配套 `application-source.zip` 中含构建脚本、资源和依赖版本。

## 替换

1. 关闭应用并复制整个目录作为工作副本。
2. 根据源档案内的 CMake / PySide 构建说明，用兼容的 Windows x64 MSVC 工具链生成共享库。保留原始许可与修改说明。Qt Base 至少构建 Core、Gui、Widgets，Qt SVG 构建 SVG；PySide Setup 中包含 PySide 和 Shiboken 的构建脚本。
3. 按 `manifest.json` 所列实际路径替换：Qt DLL 一般位于 `_internal/`，Python 扩展位于 `_internal/PySide6/`，Qt 插件位于其 `plugins/`。替换 PySide/Shiboken 时同步替换其 Python 扩展和支持 DLL，确保 Python 稳定 ABI、Qt ABI 及编译器运行库兼容。
4. 启动副本验证界面、中文输入及离线搜索。程序没有将这些 DLL 的哈希作为启动许可条件；随包哈希仅用于核验文件来源与交付完整性。
5. 如需要调整应用导入或重新打包，解压 `application-source.zip`，把本配套目录的 `licenses/`、`sources/`、`source-manifest.json` 和 `REBUILD.md` 复制到解压目录的 `third-party/` 下。安装 Python 3.12 与其中锁定的构建依赖，运行 `scripts/build-windows.ps1`。修改库构建及插件范围时，相应更新裁剪清单和来源声明；清单是构建工具，可在 MIT 授权下修改。

上游文档：<https://doc.qt.io/qt-6.8/windows-building.html>；<https://doc.qt.io/qtforpython-6.8/building_from_source/index.html>。完整源码已随本目录提供，链接不作为唯一的源码获取方式。
