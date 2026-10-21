"""Exercise the real public EXE using a disposable, separate public data home."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from railnomad.storage import Store, encode
from railnomad.domain.model import Request
from railnomad.domain.search import new_run


def verify(exe, day):
    folder = ROOT / 'dist/verification'
    folder.mkdir(parents=True, exist_ok=True)
    exe = exe.resolve()
    with tempfile.TemporaryDirectory(prefix='native-public-', dir=folder) as temporary:
        home = Path(temporary)
        env = {k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','QT_PLUGIN_PATH','QML2_IMPORT_PATH','RAILNOMAD_HOME')}
        env.update(RAILNOMAD_PUBLIC_HOME=str(home), RAILNOMAD_OFFLINE='1')
        flags = dict(env=env, timeout=120, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert subprocess.run([str(exe),'--offline','--smoke','--smoke-page','help'],**flags).returncode == 0
        shutil.copyfile(home/'desktop-smoke.png',folder/'desktop-public-help.png')
        store = Store(home)
        snapshot = store.snapshot()
        assert len(store.setting('city_mapping:'+snapshot['id'])) == 3146
        request = Request('大连市','哈尔滨市',origin_kind='city',destination_kind='city',departure_date=day,min_nights=1,max_nights=1)
        run = new_run(store,snapshot['id'],request)
        def job(name, **values):
            path=home/(name+'.json');path.write_text(encode(dict(operation=name,**values)),encoding='utf-8')
            result=subprocess.run([str(exe),'--offline','--worker',str(path)],**flags)
            event=json.loads(Path(str(path)+'.events').read_text(encoding='utf-8').splitlines()[-1])
            return result.returncode,event
        started=time.monotonic();code,event=job('search',run_id=run)
        assert code==0 and event['event']=='done',event
        count=len(store.rows('SELECT id FROM results WHERE run_id=?',(run,)))
        assert count==4,count
        seconds=time.monotonic()-started
        identity=store.rows('SELECT id FROM results WHERE run_id=?',(run,))[0]['id']
        code,event=job('fare',result_id=identity,date=day)
        assert code==1 and event['event']=='error' and '公开发行版' in event['message'],event
        assert not (home/'fares').exists()
        (home/'exported').mkdir()
        code,event=job('export',run_id=run,path=str(home/'exported'),filters={},scope='all')
        assert code==0 and event['event']=='done',event
        assert (Path(event['result'])/'DATA-LICENSES.txt').is_file()
        assert subprocess.run([str(exe),'--offline','--smoke','--smoke-run',run],**flags).returncode==0
        shutil.copyfile(home/'desktop-smoke.png',folder/'desktop-public-results.png')
        report=dict(edition='public-offline',date=day,real_executable=True,results=count,search_wall_seconds=seconds,
                    reference_stations=3146,automatic_lookup_rejected=True,no_quote_cache=True,export_verified=True,
                    executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
    (folder/'desktop-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(encode(report))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--exe',type=Path,default=ROOT/'dist/public/RailNomad/RailNomad.exe')
    parser.add_argument('--date',default=datetime.now(timezone(timedelta(hours=8))).date().isoformat())
    args=parser.parse_args()
    verify(args.exe,args.date)
