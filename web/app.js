import {renderItinerary} from './itinerary.js';
import {LocalProvider,RemoteProvider} from './providers.js';
const $=id=>document.getElementById(id);
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=value=>Number(value||0).toLocaleString('zh-CN');
const money=value=>value===null||value===undefined?'待核价':'¥'+Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2});
const duration=seconds=>{const m=Math.round(seconds/60);return `${Math.floor(m/60)}小时${m%60?m%60+'分':''}`};
const clock=seconds=>`第${Math.floor(seconds/86400)+1}天 ${String(Math.floor(seconds%86400/3600)).padStart(2,'0')}:${String(Math.floor(seconds%3600/60)).padStart(2,'0')}`;
const split=value=>value.trim().split(/[\s,，、;；]+/).filter(Boolean);
const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const state={mode:'local',busy:false,meta:null,page:0,groups:[],result:null,queryVersion:0,filterTimer:null,composing:false};
let toastTimer,authMode='login';
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4500)}
function showError(message){$('form-error').textContent=message;$('form-error').hidden=!message}
function showInfo(title,html){$('info-title').textContent=title;$('info-content').innerHTML=html;$('info-dialog').showModal()}
let confirmResolve;
function confirmAction(title,html){$('confirm-title').textContent=title;$('confirm-content').innerHTML=html;$('confirm-dialog').showModal();return new Promise(resolve=>confirmResolve=resolve)}
function finishConfirm(answer){$('confirm-dialog').close();confirmResolve?.(answer);confirmResolve=null}
$('confirm-yes').onclick=()=>finishConfirm(true);$('confirm-no').onclick=()=>finishConfirm(false);$('confirm-dialog').addEventListener('cancel',event=>{event.preventDefault();finishConfirm(false)});
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>button.closest('dialog').close());
function progress(event){
  if(!state.busy)return;
  $('progress-stage').textContent=event.stage||'正在搜索';
  const parts=[];if(event.routes!==undefined)parts.push(`已找到 ${num(event.routes)} 条候选`);
  $('progress-detail').textContent=parts.join(' · ')||event.detail||'正在匹配车次和日期…';
}
const local=new LocalProvider(progress),remote=new RemoteProvider(progress);
function provider(){return state.mode==='local'?local:remote}
function setBusy(busy){state.busy=busy;$('search-button').disabled=busy||!state.meta;$('search-button').innerHTML=busy?'正在寻找路线…':'搜索路线 <span aria-hidden="true">→</span>';$('cancel-button').hidden=!busy;$('local-mode').disabled=busy;$('remote-mode').disabled=busy;$('connect-button').disabled=busy;document.querySelectorAll('#result-panel input,#result-panel select,#result-panel button').forEach(e=>e.disabled=busy)}
function resetResults(){state.queryVersion++;state.result=null;state.groups=[];state.page=0;$('routes').replaceChildren();$('result-panel').hidden=true;$('welcome-panel').hidden=false;$('progress-panel').hidden=true}
async function setMode(mode){
  if(mode!=='local')return;
  if(state.busy||mode===state.mode)return;
  if(mode==='remote'&&!await confirmAction('切换到后端模式',`<p>将清除本机结果，切换到需登录的后端模式。本机结果不会上传。</p>`))return;
  if(state.mode==='local')local.destroy(new Error('已切换模式，本机会话结果已清除。'));
  state.mode=mode;resetResults();showError('');
  $('local-mode').classList.toggle('active',mode==='local');$('remote-mode').classList.toggle('active',mode==='remote');$('local-mode').setAttribute('aria-pressed',mode==='local');$('remote-mode').setAttribute('aria-pressed',mode==='remote');$('remote-banner').hidden=mode!=='remote';
  $('mode-note').textContent=mode==='local'?'参数仅留在本机':'确认后才发送参数到后端';
  refreshDataStatus();if(mode==='remote'&&!remote.connected)$('connect-dialog').showModal();
}
$('local-mode').onclick=()=>setMode('local');$('remote-mode').onclick=()=>setMode('remote');$('connect-button').onclick=()=>$('connect-dialog').showModal();
$('connect-form').onsubmit=async event=>{
  event.preventDefault();$('connect-error').hidden=true;$('login-button').disabled=true;
  try{if(authMode==='register'&&$('password').value!==$('repeat-password').value)throw new Error('两次输入的密码不一致');await remote[authMode]($('api-url').value.trim(),$('username').value.trim(),$('password').value);$('password').value='';$('repeat-password').value='';$('connect-dialog').close();$('remote-status').textContent=`已连接 · ${new URL(remote.url).host}`;$('connect-button').textContent='连接设置';$('logout-button').hidden=false;refreshDataStatus();toast('已连接后端')}
  catch(error){$('connect-error').textContent=error.message;$('connect-error').hidden=false}
  finally{$('login-button').disabled=false}
};
for(const mode of ['login','register'])$('auth-'+mode).onclick=()=>{authMode=mode;$('repeat-password-label').hidden=mode!=='register';$('repeat-password').required=mode==='register';$('login-button').textContent=mode==='register'?'注册并登录':'连接并登录';$('password').autocomplete=mode==='register'?'new-password':'current-password';$('auth-login').classList.toggle('active',mode==='login');$('auth-register').classList.toggle('active',mode==='register');$('connect-error').hidden=true};
$('logout-button').onclick=()=>{if(state.busy){toast('请先等待或取消当前任务');return}remote.logout();$('password').value='';$('logout-button').hidden=true;$('remote-status').textContent='尚未连接后端';if(state.mode==='remote')resetResults();refreshDataStatus();toast('已退出后端连接')};

function refreshDataStatus(){if(!state.meta)return;const s=state.mode==='remote'&&remote.connected?remote.snapshot:state.meta.snapshot;$('data-status').textContent=state.mode==='remote'&&!remote.connected?'后端模式 · 请先连接服务':`${state.mode==='local'?'本机':'后端'}数据已就绪`}
function syncPlaceList(){for(const field of ['origin','destination'])$(field).setAttribute('list',$(field+'-kind').value==='city'?'cities':'stations')}
for(const field of ['origin','destination'])$(field+'-kind').onchange=syncPlaceList;
$('swap').onclick=()=>{[$('origin').value,$('destination').value]=[$('destination').value,$('origin').value];[$('origin-kind').value,$('destination-kind').value]=[$('destination-kind').value,$('origin-kind').value];syncPlaceList()};
$('all-dates').onchange=()=>{$('departure-date').disabled=$('all-dates').checked;$('departure-date').required=!$('all-dates').checked};
for(const prefix of ['daily','first','last'])$(prefix+'-limit').onchange=()=>{$(prefix+'-custom').hidden=$(prefix+'-limit').value!=='custom';$(prefix+'-custom').required=$(prefix+'-limit').value==='custom'};
$('example-button').onclick=()=>{$('origin').value='大连市';$('destination').value='哈尔滨市';$('origin-kind').value=$('destination-kind').value='city';$('min-nights').value=$('max-nights').value='1';$('all-dates').checked=false;$('all-dates').onchange();syncPlaceList();$('origin').focus();toast('已填入体验行程')};
function resolveLocal(value,kind){const normalized=value.trim();const items=kind==='city'?state.meta.cities.map(name=>({name,id:name})):state.meta.stations;const matches=items.filter(item=>item.id===normalized||item.name===normalized||(kind==='city'&&item.name.replace(/市$/,'')===normalized));if(matches.length!==1)throw new Error(`无法唯一识别“${normalized}”，请从${kind==='city'?'城市':'车站'}建议中选择完整名称${kind==='station'?'或使用站点代码':''}`);return kind==='city'?matches[0].name:matches[0].id}
function resolveCities(value){return split(value).map(token=>resolveLocal(token,'city'))}
function readRequest(){
  const data=new FormData($('planner-form')),values={};
  for(const [key,value] of data){if(['origin','destination','allow_short_hsr','consecutive'].includes(key))continue;if(['required_city_mode'].includes(key))values[key]=value;else if(value.includes(':')){const [h,m]=value.split(':').map(Number);values[key]=h*60+m}else values[key]=Number(value)}
  values.origin=$('origin').value.trim();values.destination=$('destination').value.trim();values.origin_kind=$('origin-kind').value;values.destination_kind=$('destination-kind').value;
  values.departure_date=$('all-dates').checked?null:$('departure-date').value;
  values.allow_short_hsr=$('short-hsr').checked;values.consecutive=$('consecutive').checked;
  function minutes(prefix){const selected=$(prefix+'-limit').value;return selected===''?null:Number(selected==='custom'?$(prefix+'-custom').value:selected)}
  values.max_daylight_on_train=minutes('daily');values.limit_daylight=values.max_daylight_on_train!==null;values.first_daylight_limit=minutes('first');values.last_daylight_limit=minutes('last');
  if(state.mode==='local'){values.origin=resolveLocal(values.origin,values.origin_kind);values.destination=resolveLocal(values.destination,values.destination_kind);values.required_cities=resolveCities($('required-cities').value);values.forbidden_cities=resolveCities($('forbidden-cities').value);values.preferred_cities=resolveCities($('preferred-cities').value)}
  else{values.required_cities=split($('required-cities').value);values.forbidden_cities=split($('forbidden-cities').value);values.preferred_cities=split($('preferred-cities').value)}
  if(values.min_nights>values.max_nights)throw new Error('最少夜数不能大于最多夜数');
  if(values.departure_date&&values.departure_date<today)throw new Error('请选择北京时间今天或以后的出发日期');
  if(state.mode==='local'&&values.departure_date&&!state.meta.departure_dates.includes(values.departure_date))throw new Error('本机运行图没有覆盖该实际出发日期，请选择数据覆盖范围内的日期');
  return values;
}
$('planner-form').onsubmit=async event=>{
  event.preventDefault();if(state.busy)return;showError('');
  let request;try{request=readRequest()}catch(error){showError(error.message);return}
  if(state.mode==='remote'){
    if(!remote.connected){$('connect-dialog').showModal();return}
    if(!await confirmAction('确认发送搜索参数',`<p>发送到 <b>${escape(remote.url)}</b></p><p>${escape(request.origin)} → ${escape(request.destination)}<br>${escape(request.departure_date||'全部适用日期')} · ${request.min_nights}—${request.max_nights} 晚 · 预算 ${money(request.budget)}</p><details><summary>查看全部参数</summary><pre>${escape(JSON.stringify(request,null,2))}</pre></details>`))return;
  }
  resetResults();setBusy(true);$('progress-panel').hidden=false;$('progress-panel').classList.remove('done');progress({stage:'准备搜索',detail:state.mode==='local'?'正在准备本机计算环境…':'正在确认后端数据和参数…'});
  const activeProvider=provider();
  try{
    if(state.mode==='remote'){
      request.origin=await remote.resolve(request.origin,request.origin_kind);request.destination=await remote.resolve(request.destination,request.destination_kind);
      for(const key of ['required_cities','forbidden_cities','preferred_cities'])request[key]=await Promise.all(request[key].map(value=>remote.resolve(value,'city')));
    }
    const validated=await activeProvider.validate(request);const warnings=validated.warnings||[];
    if(warnings.length&&!await confirmAction('这次搜索可能需要更久',`<ul>${warnings.map(w=>`<li>${escape(w)}</li>`).join('')}</ul><p>最多保存 30 万条；普通搜索每次最多 20 分钟，含短高铁 10 分钟。达到限制时保留不完整标识。本机模式首次试用建议先选单日、较少夜数。</p>`)){resetResults();return}
    progress({stage:'开始搜索',detail:'校验日历、车次和换乘窗口。'});
    state.result=await activeProvider.start(validated.request||request);
    $('welcome-panel').hidden=true;$('result-panel').hidden=false;
    const audit=state.result.audit||{};const status=state.result.status||audit.status;
    $('progress-panel').classList.add('done');$('progress-stage').textContent=status==='completed'?'本次搜索已完成':status==='limited'?'搜索达到资源上限':status==='cancelled'?'任务已取消':'任务已结束';
    $('progress-detail').textContent=`保存 ${num(audit.complete_candidates)} 条候选${state.result.seconds!==undefined?' · 计算 '+state.result.seconds.toFixed(1)+' 秒':''}`;
    $('progress-panel').hidden=true;$('completion-badge').textContent=status==='completed'?`已完成${state.result.seconds!==undefined?' · '+state.result.seconds.toFixed(1)+'秒':''}`:'部分结果 · 未完成';$('completion-badge').classList.toggle('warn',status!=='completed');
    $('result-notice').hidden=status==='completed';$('result-notice').textContent=audit.resource_stop||'本次尚未搜完，以下为已保存结果。';
    $('result-search').value='';$('result-sort').value='comfort';$('known-price').checked=false;state.page=0;await renderResults();
  }catch(error){showError(error.message);$('progress-panel').hidden=true;if(state.mode==='local'&&error.name!=='AbortError')toast('仍在本机模式，没有自动发送参数到后端。')}
  finally{setBusy(false);updatePaging()}
};
$('cancel-button').onclick=async()=>{
  if(!state.busy)return;
  if(state.mode==='local'){
    if(!await confirmAction('停止本机计算？','<p>将立即终止计算进程。本次尚未完成的搜索和本机会话结果会清除，参数会保留，可以重新搜索。</p>'))return;
    local.destroy();resetResults();showError('已停止本机计算，本次会话结果已清除。参数未发送到后端。');
  }else{try{await remote.cancel();progress({stage:'已请求取消',detail:'等待后端安全停止，已保存结果由服务保留。'});$('cancel-button').disabled=true}catch(error){toast(error.message)}}
};
function currentFilters(){let cities=split($('result-search').value);if(state.mode==='local')cities=cities.map(token=>{try{return resolveLocal(token,'city')}catch{return token}});return {cities,known_price:$('known-price').checked}}
function pathTitle(pattern){const list=Array.isArray(pattern)?pattern:JSON.parse(pattern);return list.map(c=>escape(c.replace(/市$/,''))).join('<span>→</span>')}
let resultQueryRunning=false,resultQueryAgain=false;
async function renderResults(){
  state.queryVersion++;
  if(resultQueryRunning){resultQueryAgain=true;return}
  resultQueryRunning=true;
  try{do{resultQueryAgain=false;await fetchAndRenderResults()}while(resultQueryAgain&&state.result)}
  finally{resultQueryRunning=false}
}
async function fetchAndRenderResults(){
  if(!state.result)return;const revision=state.queryVersion;
  const response=await provider().results({page:state.page,sort:$('result-sort').value,filters:currentFilters()});if(revision!==state.queryVersion)return;
  if(state.page>0&&!response.items?.length){state.page=0;return renderResults()}
  state.groups=response.items||[];state.totalPages=response.total_pages??Math.ceil(response.total_items/(response.page_size||12));
  $('result-count').textContent=`${num(response.total_variants??response.total_items)} 条具体方案 · ${num(response.total_items)} 种城市走向`;
  $('routes').innerHTML=state.groups.length?state.groups.map((g,index)=>`<article class="route-card"><div class="route-top"><div><h3>${pathTitle(g.pattern)}</h3><div class="route-stats"><span class="badge">${num(g.variants)} 种搭配</span><span>${g.min_nights===g.max_nights?escape(g.min_nights):escape(g.min_nights)+'—'+escape(g.max_nights)} 晚卧铺</span><span>换站 ${escape(g.min_changes)}${g.max_changes>g.min_changes?'—'+escape(g.max_changes):''} 次</span></div></div><div class="route-price">${money(g.price)}${g.price===null||g.price===undefined?'':'<small>学生参考价起</small>'}</div></div><details class="route-link" data-group="${index}"><summary>查看车次</summary><div class="variant-list"></div></details></article>`).join(''):'<div class="empty-state"><h3>没有匹配路线</h3><p>试试清除筛选，或重新搜索。</p></div>';
  updatePaging();
}
function updatePaging(){$('page-label').textContent=state.totalPages?`${state.page+1} / ${state.totalPages} 页`:'0 / 0 页';$('prev-page').disabled=state.busy||state.page<=0;$('next-page').disabled=state.busy||!state.totalPages||state.page>=state.totalPages-1}
function scheduleFilters(){clearTimeout(state.filterTimer);state.filterTimer=setTimeout(()=>{state.page=0;renderResults().catch(e=>toast(e.message))},250)}
$('result-search').addEventListener('compositionstart',()=>{state.composing=true;clearTimeout(state.filterTimer)});$('result-search').addEventListener('compositionend',()=>{state.composing=false;scheduleFilters()});$('result-search').addEventListener('input',event=>{if(!state.composing&&!event.isComposing)scheduleFilters()});
for(const id of ['result-sort','known-price'])$(id).onchange=()=>{clearTimeout(state.filterTimer);state.page=0;renderResults().catch(e=>toast(e.message))};
$('prev-page').onclick=()=>{state.page--;renderResults().catch(e=>toast(e.message))};$('next-page').onclick=()=>{state.page++;renderResults().catch(e=>toast(e.message))};
async function loadVariants(detail,page=0){
  const group=state.groups[Number(detail.dataset.group)];if(!group)return;const list=detail.querySelector('.variant-list');list.innerHTML='<p class="muted small">正在读取具体方案…</p>';
  try{const result=await provider().results({pattern:typeof group.pattern==='string'?group.pattern:JSON.stringify(group.pattern),filters:currentFilters(),sort:$('result-sort').value,page});if(!detail.isConnected)return;
    list.innerHTML=(result.items||[]).map(r=>`<article class="variant"><div class="variant-head"><strong>${r.trains.map(escape).join(' → ')}</strong><div class="route-price">${money(r.known_price)}${r.known_price===null||r.known_price===undefined?'':'<small>学生参考</small>'}</div></div><div class="variant-meta">${escape(r.start_station)} → ${escape(r.end_station)} · ${escape(r.nights)} 晚 · 全程 ${duration(r.duration_seconds)}${r.short_hsr?' · 含短高铁':''}</div><div class="variant-meta">${clock(r.first_departure)} 出发 · ${clock(r.last_arrival)} 抵达</div><div class="route-stats">${r.comfort_minutes?'<span class="badge warn">超出舒适时间</span>':'<span class="badge">舒适时段</span>'}${r.empty_nights?.length?'<span class="badge warn">含空夜</span>':''}</div><details data-result="${escape(r.id)}"><summary>行程详情</summary><div class="detail-body"></div></details></article>`).join('');
    const pages=result.total_pages??Math.ceil(result.total_items/8);if(pages>1)list.insertAdjacentHTML('beforeend',`<div class="variant-pagination"><button class="secondary" data-variant-page="${page-1}" ${page===0?'disabled':''}>上一页搭配</button><span>${page+1} / ${pages}</span><button class="secondary" data-variant-page="${page+1}" ${page>=pages-1?'disabled':''}>下一页搭配</button></div>`);detail.dataset.loaded='true';
  }catch(error){list.textContent=error.message;delete detail.dataset.loaded}
}
async function loadDetail(detail){
  const body=detail.querySelector('.detail-body');body.textContent='正在读取行程详情…';
  try{const result=await provider().detail(detail.dataset.result);if(!detail.isConnected)return;
    detail.itinerary=result;body.innerHTML=renderItinerary(result,null,state.mode==='local');detail.dataset.loaded='true';
  }catch(error){body.textContent=error.message}
}
$('routes').addEventListener('change',event=>{if(!event.target.matches('.actual-date'))return;const detail=event.target.closest('[data-result]');detail.querySelector('.detail-body').innerHTML=renderItinerary(detail.itinerary,event.target.value,state.mode==='local')});
$('routes').addEventListener('toggle',event=>{const detail=event.target;if(detail.tagName!=='DETAILS'||!detail.open||detail.dataset.loaded)return;if(detail.dataset.group!==undefined)loadVariants(detail);else if(detail.dataset.result)loadDetail(detail)},true);
$('routes').addEventListener('click',event=>{const button=event.target.closest('[data-variant-page]');if(button&&!button.disabled)loadVariants(button.closest('[data-group]'),Number(button.dataset.variantPage))});
$('routes').addEventListener('click',async event=>{
  const button=event.target.closest('[data-check-fare]');if(!button||state.mode!=='remote'||state.busy)return;
  const detail=button.closest('[data-result]'),payload=detail.itinerary.payload||detail.itinerary;
  const date=detail.querySelector('.actual-date')?.value||payload.dates?.[0];
  if(!date){toast('该方案没有可核价的实际日期');return}
  if(!await confirmAction('确认主动核价',`<p>将向 ${escape(remote.url)} 提交当前方案和实际出发日 ${escape(date)}，由该服务查询票价。这可能消耗服务的核价额度。</p>`))return;
  setBusy(true);$('progress-panel').hidden=false;$('progress-panel').classList.remove('done');progress({stage:'提交核价任务',detail:'等候后端返回日期化报价…'});
  try{await remote.checkFare(payload.id,date);await loadDetail(detail);$('progress-stage').textContent='核价任务完成';$('progress-detail').textContent='已重新读取当前方案的证据，请核对报价日期、来源和余票。';toast('已读取更新后的票价证据')}
  catch(error){toast(error.message);$('progress-stage').textContent='核价未完成';$('progress-detail').textContent=error.message}
  finally{$('progress-panel').classList.add('done');setBusy(false);updatePaging()}
});

$('help-button').onclick=()=>showInfo('使用帮助','<p>本机计算免登录，刷新后结果清除。</p><p>此公开站点仅在浏览器计算，不提供登录或自动核价。</p><p>点击路线查看车次，展开详情查看时间和票价。</p>');
$('data-button').onclick=()=>{if(!state.meta)return;const s=state.meta.snapshot;showInfo('运行图与数据来源',`<p>当前${state.mode==='local'?'本机':'网页附带本机'}快照：<b>${escape(s.tag)}</b></p><p>有效日历：${escape(s.calendar[0])} — ${escape(s.calendar.at(-1))}<br>规则版本：${escape(state.meta.rules_version)}<br>站城映射：${num(state.meta.mapping_count)} 站</p><p>运行图来自社区项目，不等于铁路官方确认。未知站城归属、时刻异常、票价和卧铺证据均保留独立缺口。</p><p><a href="https://github.com/wensimehrp/chinese-railway-gtfs" target="_blank" rel="noopener noreferrer">chinese-railway-gtfs ↗</a> · 上游声明 CC BY 4.0；坐标归属 © OpenStreetMap contributors（ODbL）。SQLite 转换和时刻质量处理由本项目执行。</p>`)};
async function boot(){
  try{const response=await fetch('./data/manifest.json');if(!response.ok)throw new Error('网页数据未准备，请先运行网页构建命令。');state.meta=await response.json();
    $('cities').innerHTML=state.meta.cities.map(name=>`<option value="${escape(name)}"></option>`).join('');$('stations').innerHTML=state.meta.stations.map(s=>`<option value="${escape(s.name)}">${escape(s.id)}</option>`).join('');
    const dates=state.meta.departure_dates.filter(d=>d>=today);$('departure-date').min=dates[0]||today;$('departure-date').max=dates.at(-1)||today;$('departure-date').value=dates[0]||today;
    $('date-help').textContent=dates.length?`可选 ${dates[0]} 至 ${dates.at(-1)}`:'此快照已无今天或以后的出发日，需要更新运行图。';
    refreshDataStatus();setBusy(false);
  }catch(error){showError(error.message);$('data-status').textContent='本机数据未就绪';$('search-button').disabled=true}
}
if(location.protocol==='file:'){showError('请通过“启动网页.cmd”打开；浏览器无法从本地文件地址载入计算进程。');$('search-button').disabled=true}else boot();
