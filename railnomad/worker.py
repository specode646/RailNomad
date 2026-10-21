import json
import logging
import os
import time
from pathlib import Path

from .storage import Store
from .data.release import Cancelled
from .resources import ResourceLimit


def execute(job_path, home):
    job = json.loads(Path(job_path).read_text(encoding='utf-8-sig'))
    store = Store(home)
    parent_handle = None
    if os.name == 'nt' and job.get('parent_pid'):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        parent_handle = kernel.OpenProcess(0x00100000, False, job['parent_pid'])
    last_parent_check = [0.0,False]
    def cancelled():
        if Path(job_path + '.cancel').exists():return True
        current=time.monotonic()
        if parent_handle and current-last_parent_check[0]>.25:
            last_parent_check[:]=[current,kernel.WaitForSingleObject(parent_handle,0)==0]
        return last_parent_check[1]


    def emit(**event):
        # File-backed JSONL works in PyInstaller windowed builds where stdout
        # is intentionally unavailable. Each GUI owns its worker journal.
        with open(job_path + '.events', 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=True) + '\n')
            stream.flush()

    try:
        operation = job['operation']
        if operation == 'latest':
            from .data.release import latest
            result = latest(store, cancelled)
        elif operation == 'download':
            from .data.release import download
            from .data.gtfs import import_gtfs
            source = download(job['asset'], home, emit, cancelled)
            result = import_gtfs(source, store, job['metadata'], emit, cancelled)
        elif operation == 'import':
            from .data.gtfs import import_gtfs
            result = import_gtfs(job['path'], store, job.get('metadata'), emit, cancelled)
        elif operation == 'mapping':
            from .data.mapping import import_mapping
            result=import_mapping(store,job['snapshot'],job['path'],emit,cancelled)
        elif operation == 'osm_mapping':
            from .data.osm_mapping import install_package
            result=install_package(store,job['snapshot'],job['path'],emit,cancelled)
        elif operation == 'evidence':
            from .evidence import import_evidence
            result=import_evidence(store,job['snapshot'],job['path'],emit,cancelled)
        elif operation == 'search':
            from .domain.search import search
            result = search(store, job['run_id'], emit, cancelled)
        elif operation == 'export':
            from .export import export_run
            result = export_run(store, job['run_id'], job['path'], job['filters'], job['scope'], emit, cancelled)
        elif operation == 'fare':
            from .fares import price_route,sync_run_fares
            result=price_route(store,job['result_id'],job['date'],emit,cancelled)
            run=store.rows('SELECT run_id FROM results WHERE id=?',(job['result_id'],))[0]['run_id']
            sync_run_fares(store,run,emit,cancelled)
        elif operation == 'fare_batch':
            from .fares import price_routes,sync_run_fares
            result=price_routes(store,job['result_ids'],job['date'],emit,cancelled)
            for run in {r['run_id'] for identity in job['result_ids'] for r in store.rows('SELECT run_id FROM results WHERE id=?',(identity,))}:sync_run_fares(store,run,emit,cancelled)
        elif operation == 'sync_fares':
            from .fares import sync_run_fares
            result=sync_run_fares(store,job['run_id'],emit,cancelled)
        elif operation == 'delete':
            from .deletion import delete
            result=delete(store,job['scope'],job['run_ids'],job.get('result_ids',[]),job.get('patterns',[]),emit,cancelled)
        else:
            raise ValueError('未知后台操作')
        emit(event='done', result=result)
        return 0
    except ResourceLimit as exc:
        if job.get('operation')=='search':
            from .storage import encode
            row=store.rows('SELECT audit FROM runs WHERE id=?',(job['run_id'],))
            result=json.loads(row[0]['audit']) if row else {}
            result.update(status='limited',resource_stop=str(exc))
            with store.connect() as db:db.execute("UPDATE runs SET status='limited',audit=? WHERE id=?",(encode(result),job['run_id']))
            emit(event='done',result=result);return 0
        emit(event='error',code='ResourceLimit',message=str(exc),retryable=True);return 1
    except Cancelled as exc:
        emit(event='cancelled', message=str(exc))
        return 0
    except Exception as exc:
        logging.exception('Background operation failed: %s', job.get('operation'))
        if job.get('operation') == 'search':
            with store.connect() as db:
                db.execute("UPDATE runs SET status='failed' WHERE id=? AND status!='completed'",(job['run_id'],))
        emit(event='error', code=type(exc).__name__, message=str(exc), retryable=not isinstance(exc,(ValueError,KeyError,TypeError)),
             log=str(home / 'logs' / 'railnomad.log'))
        return 1

    finally:
        if parent_handle:
            kernel.CloseHandle(parent_handle)
