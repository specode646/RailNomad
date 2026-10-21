"""Reject unused Qt add-on binaries in the desktop package."""
import sys
from pathlib import Path

UNUSED = {'qt6virtualkeyboard.dll', 'qtvirtualkeyboardplugin.dll', 'qvirtualkeyboardplugin.dll',
          'qt6charts.dll', 'qt6datavisualization.dll', 'qt6graphs.dll'}


def check(root):
    found = sorted(str(path.relative_to(root)) for path in root.rglob('*')
                   if path.is_file() and path.name.casefold() in UNUSED)
    if found:
        raise ValueError('打包带入未使用的 Qt 附加模块，请在干净的 Essentials 环境重新构建：\n' + '\n'.join(found))


if __name__ == '__main__':
    check(Path(sys.argv[1]))
