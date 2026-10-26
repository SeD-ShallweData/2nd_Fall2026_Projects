/* Hash routing, shared region state and request utilities. No analysis rules. */
(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const pages={overview:['서비스 개요','지역의 신호를 읽고, 다음 검토로 연결하세요.'],signals:['R/O/A 지역 신호','행정동별 신호 조합을 비교하고, 수치와 기간 근거를 확인하세요.'],activity:['SK 활동 탐색','강남·춘천의 공간, 연령, 시간대와 요일별 활동을 탐색하세요.'],welfare:['복지자원 탐색','지역의 사업을 찾고, 대상·기간·실제 출처를 검토하세요.'],agent:['AI Agent','선택한 지역에 질문하고, 확인된 근거로 검토를 이어가세요.']};
  const controllers=new Map(), memo=new Map(), scripts=new Map();
  let context={city:'11230',dong:''}, page='', contextTicket=0, roaCatalog;
  try{const saved=JSON.parse(sessionStorage.getItem('bbigeudeok.context.v1'));if(saved&&['11230','32010'].includes(saved.city)&&/^\d{8}$|^$/.test(saved.dong))context=saved;}catch{}
  const safe=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function requireDemoLogin(){let notice=el('demo-auth-required');if(!notice){notice=document.createElement('div');notice.id='demo-auth-required';notice.className='panel';notice.setAttribute('role','alert');notice.style.cssText='position:sticky;top:0;z-index:2000;margin:12px;padding:16px;border:2px solid #a95435;background:#fff;';const text=document.createElement('p');text.textContent='외부 시연의 접속 인증을 다시 확인해야 합니다. 입력한 질문을 보관한 뒤 전달받은 전용 접속 링크로 다시 열어 주세요.';const link=document.createElement('a');link.className='button primary';link.textContent='접속 안내 열기';link.href=location.origin+location.pathname+location.hash;notice.append(text,link);document.body.prepend(notice);}return Error('접속 인증을 갱신한 뒤 다시 조회하세요. 요청은 자동 재전송하지 않았습니다.');}
  const requestErrors={DEMO_RATE_LIMIT:'질문 요청이 잠시 집중되었습니다. 1분 뒤 다시 보내 주세요.',DEMO_LLM_HOURLY_LIMIT:'이 접속 세션의 시간당 질문 30회에 도달했습니다. 이전 요청으로부터 1시간이 지나면 자동으로 다시 사용할 수 있습니다.',DEMO_LLM_DAILY_LIMIT:'이 접속 세션의 하루 질문 100회에 도달했습니다. 이전 요청으로부터 24시간이 지나면 자동으로 다시 사용할 수 있습니다.',DEMO_LLM_GLOBAL_DAILY_LIMIT:'전체 하루 질문 1,000회에 도달했습니다. 이전 요청으로부터 24시간이 지나면 자동으로 다시 사용할 수 있습니다.',DEMO_AGENT_BUSY:'다른 질문에 응답 중입니다. 응답이 끝난 뒤 다시 보내 주세요.',LLM_RATE_LIMIT:'공급자 요청이 잠시 집중되었습니다. 1분 뒤 다시 보내 주세요.',LLM_DAILY_LIMIT:'하루 공급자 호출 한도에 도달했습니다. 잠시 후 다시 확인해 주세요.'};
  function requestError(data){const error=data.error;if(typeof error==='string')return error;if(typeof error?.message==='string')return error.message;return requestErrors[error?.code]||error?.code||'자료 조회 실패';}
  async function request(path,q={},payload,timeout=90000){const abort=new AbortController();const timer=setTimeout(()=>abort.abort(),timeout);const demo=location.protocol==='https:'&&(location.hostname||'').endsWith('.trycloudflare.com');try{const response=await fetch(path+(payload?'':'?'+new URLSearchParams(q)),{signal:abort.signal,...(demo?{redirect:'manual'}:{}),...(payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{})});if(demo&&(response.type==='opaqueredirect'||response.status===401))throw requireDemoLogin();const data=await response.json();if(!response.ok)throw Error(requestError(data));return data;}catch(e){throw Error(e.name==='AbortError'?'응답 시간이 초과되었습니다. 잠시 후 다시 조회하세요.':e.message);}finally{clearTimeout(timer);}}
  function cached(key,load){if(!memo.has(key))memo.set(key,load().catch(e=>{memo.delete(key);throw e;}));return memo.get(key);}
  function loadScript(src){if(!scripts.has(src))scripts.set(src,new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=src;s.onload=resolve;s.onerror=()=>{scripts.delete(src);s.remove();reject(Error('차트 라이브러리 로드 실패. 다시 진입해 주세요.'));};document.head.append(s);}));return scripts.get(src);}
  const api={get context(){return {...context};},get page(){return page;},safe,request,cached,loadScript,
    skCatalog:()=>cached('sk-catalog',()=>request('/api/catalog')),
    roaCatalog:()=>cached('roa-catalog',()=>request('/api/roa/catalog')),
    detail:code=>cached('detail:'+code,()=>request('/api/roa/detail',{dong:code})),
    register(name,controller){controllers.set(name,controller);},
    setContext(next,source='common'){
      const updated={...context,...next};if(!['11230','32010'].includes(updated.city))return;
      if(updated.city!==context.city&&!Object.hasOwn(next,'dong'))updated.dong='';
      if(updated.city===context.city&&updated.dong===context.dong)return;
      context=updated;contextTicket++;try{sessionStorage.setItem('bbigeudeok.context.v1',JSON.stringify(context));}catch{}
      refreshContext();for(const controller of controllers.values())controller.contextChanged?.({...context},source);
      document.dispatchEvent(new CustomEvent('dashboard:context',{detail:{...context}}));
    },
    invalidateAgent(){document.dispatchEvent(new Event('dashboard:invalidate-agent'));},
    error(message){el('app-error').textContent=message;el('app-error').hidden=!message;}
  };window.Dashboard=api;
  async function refreshContext(){const ticket=contextTicket;el('r-city').value=context.city;el('context-status').textContent='지역 목록 확인 중…';try{const rows=context.city==='11230'?(roaCatalog||await api.roaCatalog()).dongs.map(d=>[d.adm_cd8,d.adm_nm]):(await api.skCatalog()).dongs.filter(d=>d.CITY_CODE===context.city).map(d=>[d.DONG_CODE,d.NAME]);if(ticket!==contextTicket)return;if(context.dong&&!rows.some(r=>r[0]===context.dong)){api.setContext({dong:''});return;}el('r-dong').replaceChildren(new Option('시군구 전체',''),...rows.map(([v,t])=>new Option(t,v)));el('r-dong').value=context.dong;el('context-status').textContent=context.city==='11230'?'강남구 · R/O/A 및 SK 분석':'춘천시 · SK 활동 분석';}catch(e){if(ticket===contextTicket){el('context-status').textContent='지역 목록 조회 실패';api.error(e.message);}}}
  async function route(){let name=location.hash.slice(2);if(!Object.hasOwn(pages,name)){history.replaceState(null,'','#/overview');name='overview';}page=name;const [title,description]=pages[name];document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!=='page-'+name);document.querySelectorAll('[data-page]').forEach(a=>{if(a.dataset.page===name)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});el('page-title').textContent=title;el('page-description').textContent=description;el('breadcrumb').textContent=title;el('page-number').textContent='페이지 / 0'+(Object.keys(pages).indexOf(name)+1);document.title=title+' · 삐그덕';closeMenu();api.error('');window.scrollTo(0,0);el('main').focus({preventScroll:true});try{await controllers.get(name)?.enter?.();}catch(e){if(page===name)api.error(e.message);}}
  function closeMenu(){el('sidebar').classList.remove('is-open');el('menu-toggle').setAttribute('aria-expanded','false');el('menu-toggle').setAttribute('aria-label','메뉴 열기');}
  el('menu-toggle').onclick=()=>{const open=el('sidebar').classList.toggle('is-open');el('menu-toggle').setAttribute('aria-expanded',String(open));el('menu-toggle').setAttribute('aria-label',open?'메뉴 닫기':'메뉴 열기');if(open)document.querySelector('nav a').focus();};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeMenu();el('menu-toggle').focus();}});
  el('r-city').onchange=()=>api.setContext({city:el('r-city').value});el('r-dong').onchange=()=>api.setContext({dong:el('r-dong').value});
  document.querySelector('.skip-link').onclick=event=>{event.preventDefault();el('main').focus();};
  window.addEventListener('hashchange',route);let resizeFrame;window.addEventListener('resize',()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>controllers.get(page)?.resize?.());});
  api.register('overview',{async enter(){const c=await api.roaCatalog();el('overview-facts').innerHTML=`<div><small>R/O/A 분류 · 강남구</small><strong>${safe(c.classified_count)}개 동</strong><p>8유형 규칙 기반 · ${safe(c.hold_count)}개 동 판정 보류</p></div><div><small>공간 활동 탐색</small><strong>강남 · 춘천</strong><p>2025년 7~12월 · 행정동 / 격자 / 관측 위치</p></div><div><small>검증된 로컬 복지자료</small><strong>${safe(c.resource_count)}개</strong><p>공식 API 저장 자료는 검색 시 별도 확인</p></div>`;}});
  document.addEventListener('DOMContentLoaded',async()=>{try{roaCatalog=await api.roaCatalog();await refreshContext();}catch(e){api.error(e.message);}await route();});
})();
