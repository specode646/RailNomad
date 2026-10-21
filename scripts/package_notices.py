"""Build a license/source companion; fail on missing required materials."""
import hashlib
import importlib.metadata
import json
import shutil
import ssl
import sqlite3
import sys
import zipfile
from pathlib import Path


def package(root, project=Path(__file__).resolve().parents[1]):
    root=Path(root).resolve();materials=project/'third-party'
    required=['CPython-LICENSE.txt','CPython-History-and-License.rst','Apache-2.0.txt',
              'CC-BY-4.0.txt','ODbL-1.0.txt','libffi-LICENSE.txt','GeoCode-MIT.txt',
              'GeoCode-NOTICE.txt','cn-atlas-package.json','Microsoft-Runtime-Conditions.txt',
              'upstream/qtbase-everywhere-src-6.8.3/LICENSES/LGPL-3.0-only.txt',
              'upstream/qtbase-everywhere-src-6.8.3/LICENSES/GPL-3.0-only.txt']
    for name in required:
        if not (materials/'licenses'/name).is_file():raise ValueError('缺少许可正文或声明：'+name)
    sources=json.loads((materials/'source-manifest.json').read_text(encoding='utf-8'))
    for record in sources:
        source=materials/'sources'/record['file']
        with source.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=record['sha256']:raise ValueError('对应源码指纹不符：'+record['file'])
    for path in root.rglob('*'):
        if path.is_file() and path.suffix.casefold() in ('.geojson','.pbf','.sqlite3') and path.relative_to(root).as_posix() != '_internal/public-data/snapshot.sqlite3':
            raise ValueError('发行包混入地图或用户数据：'+str(path.relative_to(root)))
    if __package__:
        from .check_packaged_modules import check
    else:
        from check_packaged_modules import check
    check(root)
    out=root/'third-party-notices';out.mkdir(exist_ok=True)
    shutil.copytree(materials/'licenses',out/'licenses',dirs_exist_ok=True)
    for name in ['source-manifest.json','REBUILD.md']:
        shutil.copyfile(materials/name,out/name)
    source_out=out/'sources';source_out.mkdir(exist_ok=True)
    for record in sources:shutil.copyfile(materials/'sources'/record['file'],source_out/record['file'])
    for name in ['LICENSE','THIRD_PARTY_NOTICES.md']:
        shutil.copyfile(project/name,root/name)
    shutil.copyfile(project/'THIRD_PARTY_NOTICES.md',out/'README.md')
    distributions=['PySide6_Essentials','shiboken6','PyInstaller','pyinstaller-hooks-contrib']
    versions={name:importlib.metadata.version(name) for name in distributions}
    for name in distributions:
        d=importlib.metadata.distribution(name)
        for entry in d.files or []:
            if any(word in Path(entry).name.lower() for word in ('license','copying','notice')):
                source=d.locate_file(entry)
                if source.is_file():
                    target=out/'distribution-notices'/name/Path(*entry.parts)
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    with zipfile.ZipFile(out/'application-source.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for directory in ['railnomad','scripts','tests','public-data']:
            for path in (project/directory).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py','.ps1','.svg','.csv','.json','.sqlite3'):
                    archive.write(path,path.relative_to(project))
        for name in ['LICENSE','THIRD_PARTY_NOTICES.md','pyproject.toml','requirements.txt','requirements-dev.txt','run.py']:
            archive.write(project/name,name)
        archive.write(materials/'REBUILD.md','REBUILD.md')
        if (project/'README.md').is_file():archive.write(project/'README.md','README.md')
    from PySide6.QtCore import QLibraryInfo
    import pefile
    copyrights=[]
    for path in sorted((root/'_internal').rglob('*')):
        if not path.is_file() or path.suffix.casefold() not in ('.dll','.pyd'):continue
        with pefile.PE(path) as pe:
            info={}
            for group in getattr(pe,'FileInfo',[]):
                for item in group:
                    for table in getattr(item,'StringTable',[]):
                        info.update({k.decode(errors='replace'):v.decode(errors='replace') for k,v in table.entries.items()
                                     if k in (b'CompanyName',b'FileVersion',b'ProductVersion',b'LegalCopyright')})
        if info:copyrights.append(dict(path=path.relative_to(root).as_posix(),**info))
    (out/'BINARY-COPYRIGHTS.json').write_text(json.dumps(copyrights,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest=dict(python=sys.version,openssl=ssl.OPENSSL_VERSION,sqlite=sqlite3.sqlite_version,
                  qt=QLibraryInfo.build(),distributions=versions,sources=sources,
                  application_license='MIT',qt_license='LGPL-3.0-only',
                  excluded_scope=['12306 access/fare authorization'],binary_copyrights=copyrights,files=[])
    for path in sorted(root.rglob('*')):
        if path.is_file() and path!=out/'manifest.json':
            with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
            manifest['files'].append(dict(path=path.relative_to(root).as_posix(),bytes=path.stat().st_size,sha256=sha))
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


if __name__=='__main__':
    result=package(Path(sys.argv[1]))
    print(json.dumps(dict(files=len(result['files']),source_archives=len(result['sources'])),ensure_ascii=False))
