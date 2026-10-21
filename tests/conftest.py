import csv
import io
import zipfile
from pathlib import Path

import pytest
from railnomad.paths import data_home
from railnomad.storage import Store
from railnomad.data.gtfs import import_gtfs


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('RAILNOMAD_PUBLIC_HOME',str(tmp_path/'home'))
    return Store(data_home())


def fixture_zip(path, *, second_date='20270101', invalid=False):
    """Explicitly SIMULATED network, with two compatible station variants."""
    tables={
        'agency.txt':(['agency_name','agency_url','agency_timezone'],[['SIMULATED','https://example.invalid','Asia/Shanghai']]),
        'stops.txt':(['stop_id','stop_name','stop_lat','stop_lon','city'],[
            ['a','模拟甲',30,110,'甲城'],['b','模拟乙',31,111,'乙城'],['b2','模拟乙二',31,111,'乙城'],['c','模拟丙',32,112,'丙城']]),
        'routes.txt':(['route_id','route_short_name','route_type'],[['r','SIMULATED',2]]),
        'trips.txt':(['route_id','service_id','trip_id','trip_short_name'],[['r','s1','t1','Y1'],['r','s2','t2','Y2'],['r','s2','t3','Y3']]),
        'stop_times.txt':(['trip_id','arrival_time','departure_time','stop_id','stop_sequence'],[
            ['t1','20:00:00','20:00:00','a',1],['t1','32:00:00','32:00:00','b',2],
            ['t2','20:00:00','19:59:00' if invalid else '20:00:00','b',1],['t2','32:00:00','32:00:00','c',2],
            ['t3','20:00:00','20:00:00','b2',1],['t3','32:00:00','32:00:00','c',2]]),
        'calendar_dates.txt':(['service_id','date','exception_type'],[
            ['s1','20261231',1],['s1','20270101',1],['s2',second_date,1],['s2','20270102',1]])}
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name,(headers,rows) in tables.items():
            stream=io.StringIO(newline='');writer=csv.writer(stream);writer.writerow(headers);writer.writerows(rows)
            z.writestr(zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0)),stream.getvalue())
    return path


@pytest.fixture
def snapshot(store,tmp_path):
    identity=import_gtfs(fixture_zip(tmp_path/'simulated.zip'),store,{'source':'simulated'})
    store.activate(identity)
    return identity
