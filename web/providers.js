export class LocalProvider{
  constructor(onProgress){this.onProgress=onProgress;this.requests=new Map();this.counter=0;this.worker=null;this.initializing=null}
  async initialize(){
    if(this.worker)return this.initializing;
    this.worker=new Worker(new URL('./worker.js',import.meta.url),{type:'module'});
    this.worker.onmessage=({data})=>{if(data.event==='progress'){this.onProgress(data.data);return}const request=this.requests.get(data.id);if(!request)return;this.requests.delete(data.id);data.error?request.reject(new Error(data.error)):request.resolve(data.result)};
    this.worker.onerror=event=>{event.preventDefault();this.destroy(new Error('本机计算进程退出，可能是浏览器资源不足。可以减少搜索范围后重试；参数不会自动发送到后端。'))};
    this.initializing=this.call('initialize',{});
    try{return await this.initializing}catch(error){this.destroy(error);throw error}
  }
  call(operation,values){if(!this.worker)return Promise.reject(new Error('请先初始化本机环境'));return new Promise((resolve,reject)=>{const id=++this.counter;this.requests.set(id,{resolve,reject});this.worker.postMessage({id,operation,values})})}
  async validate(values){await this.initialize();return this.call('validate',values)}
  start(values){return this.call('start',values)}
  results(values){return this.call('results',values)}
  detail(id){return this.call('detail',{id})}
  destroy(error=Object.assign(new Error('已停止本机计算，本次会话中的结果已清除。'),{name:'AbortError'})){this.worker?.terminate();this.worker=null;this.initializing=null;for(const request of this.requests.values())request.reject(error);this.requests.clear()}
}

export class RemoteProvider {
  get connected(){return false}
  logout(){}
}
