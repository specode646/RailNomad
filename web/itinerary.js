export const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const money=value=>value===null||value===undefined?'待核价':'¥'+Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2});
export const duration=seconds=>{const m=Math.round(seconds/60);return `${Math.floor(m/60)}小时${m%60?m%60+'分':''}`};
export const clock=seconds=>`第${Math.floor(seconds/86400)+1}天 ${String(Math.floor(seconds%86400/3600)).padStart(2,'0')}:${String(Math.floor(seconds%3600/60)).padStart(2,'0')}`;
function day(start,offset){if(!start)return '';const date=new Date(start+'T00:00:00Z');date.setUTCDate(date.getUTCDate()+offset);return date.toISOString().slice(0,10)}
function sourceLink(source){try{const url=new URL(source);if(url.protocol==='https:'||url.protocol==='http:')return `<a href="${escape(url.href)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a>`}catch{}return escape(source||'来源待核对')}
function timeline(payload,request){
 const first=Math.floor(payload.legs[0].dep/86400),last=Math.floor(payload.legs.at(-1).arr/86400);
 const begin=(request.daylight_start??420)/14.4,end=(request.daylight_end??1200)/14.4;
 return `<div class="timeline">${Array.from({length:last-first+1},(_,i)=>{const day=first+i;return `<div class="timeline-day"><span>第 ${day+1} 天</span><div class="timeline-rail" style="background:linear-gradient(to right,#e9e9e1 0 ${begin}%,#f7edd8 ${begin}% ${end}%,#e9e9e1 ${end}%)">${payload.legs.map(leg=>{const start=Math.max(0,leg.dep-day*86400),end=Math.min(86400,leg.arr-day*86400);return end>start?`<i class="timeline-segment ${leg.short?'short':''}" style="left:${start/864}%;width:${(end-start)/864}%" title="${escape(leg.train)}"></i>`:''}).join('')}</div></div>`}).join('')}<div class="timeline-key"><span>00:00</span><span>12:00</span><span>24:00</span></div></div>`;
}
function fareEvidence(payload,selected){
 const variant=(payload.date_evidence_variants||[]).find(v=>v.start_date===selected);
 if(!variant)return '<p>此日期暂无完整票价。</p>';
 return `<p>整程学生参考：${money(variant.price_low)}${variant.price_high!==variant.price_low?' — '+money(variant.price_high):''}</p>`+variant.legs.map(item=>{
  const leg=payload.legs[item.leg];return `<p><b>${escape(leg?.train||'乘车段')} · ${escape(item.boarding_date)}</b></p>${item.fares?.length?item.fares.map(fare=>`<p>${escape(fare.seat)} / ${escape(fare.berth)} · ${fare.passenger==='student'?'学生参考':'成人参考'} ${money(fare.low)}${fare.high!==fare.low?' — '+money(fare.high):''}<br>查询于 ${escape(fare.observed_at||'待核对')} · ${sourceLink(fare.source)}</p>`).join(''):'<p>此段待核价。</p>'}`;
 }).join('');
}
export function renderItinerary(result,selectedDate,localMode){
 const payload=result.payload||result,request=result.request||{};
 const selected=selectedDate||payload.dates?.[0]||'';
 const selector=payload.dates?.length>1?`<label class="instance-label">出发日期<select class="actual-date">${payload.dates.map(date=>`<option value="${escape(date)}" ${date===selected?'selected':''}>${escape(date)}</option>`).join('')}</select></label>`:`<p class="variant-meta">出发日期：${escape(selected||'待核验')}</p>`;
 const legs=payload.legs.map((leg,index)=>{
  const next=payload.legs[index+1];return `<div class="leg"><h4>${escape(leg.train)} <span class="badge">${leg.short?'白天短高铁':`车次第 ${escape(leg.train_night||1)} 晚`}</span></h4><div class="leg-stations"><div><strong>${escape(leg.from_name)}</strong><small>${clock(leg.dep)}<br>${escape(day(selected,Math.floor(leg.dep/86400)))}</small></div><span class="leg-middle">${duration(leg.arr-leg.dep)} →</span><div><strong>${escape(leg.to_name)}</strong><small>${clock(leg.arr)}<br>${escape(day(selected,Math.floor(leg.arr/86400)))}</small></div></div>${day(selected,leg.service_day_offset||0)!==day(selected,Math.floor(leg.dep/86400))?`<div class="leg-note">列车服务日：${escape(day(selected,leg.service_day_offset||0))}</div>`:''}</div>${next?`<div class="connection">在 ${escape(leg.to_city||leg.to_name)} 停留 ${duration(next.dep-leg.arr)} · ${leg.to===next.from?'同站':'需换站'}</div>`:''}`;
 }).join('');
 return selector+timeline(payload,request)+`<div class="leg-list">${legs}</div><details class="evidence"><summary>票价与来源</summary>${fareEvidence(payload,selected)}${localMode?'':'<button type="button" class="secondary" data-check-fare>查询此日期票价</button>'}<p><a href="https://www.12306.cn/" target="_blank" rel="noopener noreferrer">12306 查询 ↗</a></p></details>`;
}
