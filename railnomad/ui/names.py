"""Resolve human place names before requests cross into the search domain."""
import json,re,sqlite3
from pathlib import Path
from collections import defaultdict
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLineEdit,QCompleter,QDialog


def tokens(text):
    return [value.strip() for value in re.split(r'[,，、;；\n]+',text) if value.strip()]


class NameCatalog:
    def __init__(self,stations=(),trains=()):
        self.stations=list(stations);self.by_id={sid:(name,city) for sid,name,city in self.stations}
        self.names=defaultdict(list);self.cities={};self.trains={};self.train_names=set()
        self.ids={sid.casefold():sid for sid in self.by_id}
        for sid,name,city in self.stations:
            self.names[name].append(sid)
            if city:
                self.cities[city]=city
                if city.endswith('市'):self.cities.setdefault(city[:-1],city)
        for identity,name in trains:
            self.trains[identity.casefold()]=identity
            for number in [name,*name.split('/')]:
                self.trains[number.casefold()]=number;self.train_names.add(number)

    @classmethod
    def load(cls,store,snapshot,mappings=None):
        snap=store.snapshot(snapshot)
        mappings=store.setting('city_mapping:'+snapshot,{}) if mappings is None else mappings
        with sqlite3.connect(snap['path']) as db:
            stations=[(sid,name,mappings.get(sid) or city) for sid,name,city in db.execute('SELECT id,name,city FROM stops ORDER BY name,id')]
            trains=list(db.execute('SELECT id,name FROM trips'))
        return cls(stations,trains)

    def resolve(self,text,kind):
        value=text.strip()
        if kind=='city':
            if value in self.cities:return self.cities[value]
            raise ValueError(f'未找到城市“{value}”，请从建议中选择完整名称')
        if kind=='train':
            value=value.removeprefix('列车').removesuffix('次').strip()
            if value.casefold() in self.trains:return self.trains[value.casefold()]
            raise ValueError(f'未找到车次“{value}”，请检查当前运行图中的车次号')
        match=re.search(r'\[([^\]]+)\]$',value)
        identity=match[1] if match else value
        if identity in self.by_id:return identity
        if identity.casefold() in self.ids:return self.ids[identity.casefold()]
        matches=self.names.get(value,[])
        if not matches and value.endswith('站'):matches=self.names.get(value[:-1],[])
        if len(matches)==1:return matches[0]
        if matches:
            choices='、'.join(f'{self.by_id[sid][0]} · {self.by_id[sid][1] or "城市未知"} [{sid}]' for sid in matches)
            raise ValueError(f'“{value}”对应多个车站，请选择：{choices}')
        raise ValueError(f'未找到车站“{value}”，请检查站名或从建议中选择')

    def resolve_many(self,text,kind):
        return list(dict.fromkeys(self.resolve(value,kind) for value in tokens(text)))

    def station_name(self,identity):return self.by_id.get(identity,(identity,''))[0]

    def choices(self,kind):
        if kind=='city':return sorted(set(self.cities.values()))
        if kind=='train':return sorted(self.train_names)
        return [f'{name} · {city or "城市未知"} [{sid}]' for sid,name,city in self.stations]


class TokenCompleter(QCompleter):
    def splitPath(self,path):return [re.split(r'[,，、;；\n]',path)[-1].strip()]

    def pathFromIndex(self,index):
        text=self.widget().text();matches=list(re.finditer(r'[,，、;；\n]',text))
        return (text[:matches[-1].end()]+' ' if matches else '')+super().pathFromIndex(index)


class NameEdit(QLineEdit):
    def __init__(self,kind='station',multiple=True,parent=None,*,picker=False,selection_help=''):
        super().__init__(parent);self.kind=kind;self.multiple=multiple;self.catalog=NameCatalog()
        self.setClearButtonEnabled(True)
        names={'station':'站名','city':'城市名称','train':'车次号'}
        self.setPlaceholderText('输入'+names[kind]+('，多个用逗号分隔' if multiple else ''))
        self.selection_help=selection_help
        if picker:
            self.select_action=self.addAction(QIcon(str(Path(__file__).parent/'assets/multi-select.svg')),QLineEdit.ActionPosition.TrailingPosition)
            self.select_action.setText('多选'+names[kind]);self.select_action.setToolTip('打开可搜索的多选列表')
            self.select_action.triggered.connect(self.choose)

    def choose(self):
        from .selection import SelectionDialog
        selected=[];unresolved=[]
        for text in tokens(self.text()):
            try:selected.append(self.catalog.resolve(text,self.kind))
            except ValueError:unresolved.append(text)
        dialog=SelectionDialog(self.catalog,self.kind,selected,unresolved[0] if unresolved else '',self.selection_help,self)
        try:
            if dialog.exec()==QDialog.DialogCode.Accepted:self.setText(dialog.selected_text())
        finally:dialog.deleteLater()

    def set_catalog(self,catalog):
        self.catalog=catalog
        completer=TokenCompleter(catalog.choices(self.kind),self) if self.multiple else QCompleter(catalog.choices(self.kind),self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains);completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setMaxVisibleItems(10);self.setCompleter(completer)

    def values(self):return self.catalog.resolve_many(self.text(),self.kind)

    def value(self):
        values=self.values()
        if len(values)!=1:raise ValueError('请填写一个明确的车站')
        return values[0]
