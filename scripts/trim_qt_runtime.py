"""Keep only the native Qt dependencies used by RailNomad's Widgets interface."""
import argparse
import json
from pathlib import Path

QT_LIBRARIES = {'qt6core.dll', 'qt6gui.dll', 'qt6widgets.dll', 'qt6svg.dll'}
QT_PLUGINS = {'platforms/qwindows.dll', 'platforms/qoffscreen.dll',
              'imageformats/qsvg.dll', 'iconengines/qsvgicon.dll',
              'styles/qmodernwindowsstyle.dll', 'styles/qwindowsvistastyle.dll'}


def trim(root):
    root = Path(root).resolve()
    qt = root / '_internal' / 'PySide6'
    if not qt.is_dir():
        raise ValueError('未找到待交付目录内的 Qt 运行库')
    removed = []
    for path in (root/'_internal').rglob('Qt6*.dll'):
        if path.name.casefold() not in QT_LIBRARIES:
            removed.append(str(path.relative_to(root)));path.unlink()
    for path in qt.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(qt).as_posix().casefold()
        drop = path.name.casefold().startswith('qt6') and path.suffix.casefold() == '.dll' and path.name.casefold() not in QT_LIBRARIES
        drop = drop or relative.startswith('plugins/') and relative[len('plugins/'):] not in QT_PLUGINS
        drop = drop or path.name.casefold() == 'opengl32sw.dll'
        drop = drop or path.suffix.casefold() == '.pyd' and path.stem not in ('QtCore','QtGui','QtWidgets')
        drop = drop or relative.startswith('translations/')  # The app installs no QTranslator.
        if drop:
            removed.append(str(path.relative_to(root)))
            path.unlink()
    # Fail if a selected DLL references another Qt module that was not retained.
    import pefile
    for path in (root/'_internal').rglob('*'):
        if not path.is_file() or path.suffix.casefold() not in ('.dll', '.pyd'):
            continue
        with pefile.PE(path) as pe:
            dependencies = list(getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])) + list(getattr(pe, 'DIRECTORY_ENTRY_DELAY_IMPORT', []))
            for dependency in dependencies:
                name = dependency.dll.decode().casefold()
                if name.startswith('qt6') and name not in QT_LIBRARIES:
                    raise ValueError(f'保留的 {path.name} 仍引用未保留的 {name}')
    return removed


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    print(json.dumps(dict(removed=trim(args.package)), ensure_ascii=False))
