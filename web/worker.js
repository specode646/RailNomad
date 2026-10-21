import {loadPyodide} from './runtime/pyodide.mjs';
let python;
function progress(stage,detail=''){self.postMessage({event:'progress',data:{stage,detail}})}
self.onmessage=async({data})=>{
  const {id,operation,values}=data;
  try{
    if(operation==='initialize'){
      progress('载入本机计算环境','首次搜索需要加载数据。');
      python=await loadPyodide({indexURL:new URL('./runtime/',self.location.href).href});
      progress('载入运行图与规则','正在加载本机数据…');
      const fetchJSON=async url=>{const r=await fetch(new URL(url,self.location.href));if(!r.ok)throw new Error(`本机数据载入失败（${r.status}）`);return r.json()};
      const [sources,config,snapshot,adapter]=await Promise.all([
        fetchJSON('./data/sources.json'),fetchJSON('./data/input.json'),
        fetch(new URL('./data/snapshot.sqlite3',self.location.href)).then(r=>{if(!r.ok)throw new Error('运行图缺失');return r.arrayBuffer()}),
        fetch(new URL('./engine.py',self.location.href)).then(r=>r.text())]);
      python.FS.mkdirTree('/data');python.FS.writeFile('/data/snapshot.sqlite3',new Uint8Array(snapshot));
      for(const file of sources){python.FS.mkdirTree('/src/'+file.path.slice(0,file.path.lastIndexOf('/')));python.FS.writeFile('/src/'+file.path,file.source)}
      python.globals.set('browser_progress',json=>self.postMessage({event:'progress',data:JSON.parse(json)}));
      await python.runPythonAsync("import sys,json;sys.path.insert(0,'/src')");
      await python.runPythonAsync(adapter);
      python.globals.set('input_json',JSON.stringify(config));
      await python.runPythonAsync("initialize(json.loads(input_json))");
      self.postMessage({id,result:{ready:true}});return;
    }
    if(!python)throw new Error('本机计算环境尚未就绪');
    python.globals.set('operation_json',JSON.stringify({operation,values}));
    const json=await python.runPythonAsync("_message=json.loads(operation_json)\njson.dumps(dispatch(_message['operation'],_message['values']),ensure_ascii=False)");
    self.postMessage({id,result:JSON.parse(json)});
  }catch(error){
    const message=String(error.message||error).split('\n').filter(Boolean).at(-1)||'本机计算失败';
    self.postMessage({id,error:message.replace(/^(ValueError|TypeError|RuntimeError):\s*/,'')});
  }
};
