/* STEP 4 UI. 유형/날짜/자격 판단은 서버 STEP 2/3 결과만 사용한다. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const safe = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = v => v === null || v === undefined ? '미확인' : Number(v).toLocaleString('ko-KR', {maximumFractionDigits:6});
  const day = v => v || '미확인';
  let catalog, geography, roaMap, group, selectedCode = '', generation = 0, resourceRequest = 0, agentRequest = 0, offset = 0;
  let provider = {}, externalStatus = {}, total = 0, resourceApi = false;
  const style = document.createElement('style');
  style.textContent = `#roa-app{margin-bottom:20px}#roa-app h2{font-size:19px}.roa-top{background:#263c58;color:#fff;padding:23px;border-radius:12px;margin-bottom:16px}.roa-top p{color:#d5e0ed}.roa-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(370px,.95fr);gap:16px}.roa-legend{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.roa-legend span{font-size:12px;border:1px solid #dde3ed;padding:6px 8px;border-radius:5px}.roa-legend i{display:inline-block;width:12px;height:12px;margin-right:5px}.roa-map{height:490px;background:#edf1f5;border-radius:8px}.roa-evidence{max-height:650px;overflow:auto}.roa-evidence h3{font-size:14px;margin:15px 0 8px}.roa-evidence td,.roa-evidence th{font-size:11px;padding:6px}.roa-signals{display:flex;gap:8px;margin:10px 0}.roa-signals b{background:#edf2f8;padding:8px;border-radius:6px}.roa-controls{display:flex;gap:12px;align-items:end;flex-wrap:wrap}.roa-controls label{min-width:150px;flex:1}.roa-controls button{width:auto}.roa-controls input{padding:9px;width:100%;border:1px solid #c6d0dd;border-radius:5px;display:block;margin-top:5px}.roa-resources{max-height:640px;overflow:auto}.roa-resource{padding:12px 0;border-top:1px solid #e2e8f0;overflow-wrap:anywhere}.roa-resource h4{font-size:13px;line-height:1.6;margin:3px 0}.roa-resource p{font-size:12px}.roa-resource button{width:auto;font-size:12px;padding:5px 10px}.roa-chat{max-height:650px;overflow:auto}.roa-chat p{font-size:12px;white-space:pre-wrap}.roa-chat h3{font-size:13px;margin:10px 0 4px}.roa-chat article{border:1px solid #dde3ed;border-radius:7px;padding:12px;margin:10px 0}.roa-chat ul{padding-left:19px}.roa-chat li{font-size:12px;line-height:1.8;margin:4px 0}#r-question{width:100%;min-height:80px;padding:10px;border:1px solid #c6d0dd;border-radius:6px;margin-top:8px}#sk-explore{margin-top:20px}#sk-explore>summary{font-size:18px;padding:18px;background:#e7edf5;border-radius:9px;margin-bottom:15px}#sk-explore .layout{display:block}#sk-explore .analysis-meta{grid-template-columns:1fr 1fr}@media(max-width:1100px){.roa-layout{grid-template-columns:1fr}.roa-evidence{max-height:none}.roa-map{height:420px}}`;
  document.head.append(style);
  style.textContent += '[hidden]{display:none!important} .roa-resource a{display:inline-block;margin-top:3px}.roa-chat article{line-height:1.7}@media(min-width:1400px){body{max-width:1800px;margin:0 auto!important}.roa-map{height:560px}.roa-layout{grid-template-columns:minmax(0,1.05fr) minmax(0,1fr)}}';
  el('roa-app').innerHTML = `
    <section class="roa-top"><h2>2025년 10~12월 기준월 종합 ROA 점검</h2><p>R/O: 기준월별 21동 중앙값 비교 · A: 같은 동의 과거 활동기간 비교</p><p>10월 ↔ SK 7~9월 / 11월 ↔ 8~10월 / 12월 ↔ 9~11월 · 관계망 직전 3개월 산정은 미확인 가정</p><p>종합 A에 SK 12월은 포함되지 않습니다. 유형·색상은 위험 순위가 아닙니다.</p></section>
    <section class="panel"><div class="roa-controls"><label>종합 점검 지역<select id="r-city"><option value="11230">강남구 · ROA + SK</option><option value="32010">춘천시 · SK 탐색</option></select></label><label>종합 점검 행정동<select id="r-dong" aria-label="종합 점검 행정동"></select></label><p id="r-status" role="status">검증된 자료 확인 중…</p></div><div id="r-legend" class="roa-legend"></div><p class="muted">색: R/O 네 패턴 · 점선 테두리: A ON · 실선: A OFF · 회색 점선: HOLD. 신호 개수로 순위를 매기지 않습니다.</p></section>
    <div id="r-unavailable" class="notice" hidden>춘천시는 SK 상세 탐색을 제공합니다. 강남구 관계망·ROA 유형·개인 지원 방향은 적용하지 않습니다.</div>
    <div id="r-map-box" class="roa-layout"><section class="panel"><h2>행정동별 ROA 조합</h2><div id="r-map" class="roa-map"></div><p class="muted">동 클릭 → 실제 수치·복지자원·Agent 맥락 연결. SK 상세 탐색 조건과 종합 유형은 독립입니다.</p></section><section class="panel roa-evidence"><h2 id="r-selected">행정동 근거</h2><div id="r-evidence">행정동을 선택하세요.</div></section></div>
    <section class="panel"><h2>복지자원 검토 조건</h2><div class="roa-controls"><label>지원 검토 시점<select id="r-mode"><option value="current">현재 지원 검토</option><option value="historical">2025년 당시 대응 · 7~11월</option></select></label><label>현재 조회 기준일<input id="r-date" type="date" required></label><label>관련 자원 검색<input id="r-query" maxlength="100" placeholder="자원명·기관·본문 검색"></label><label>조회 범위<select id="r-curated"><option value="false">전체 자원 · 선별본 우선</option><option value="true">지민 선별본 35개만</option></select></label><button id="r-search" type="button">자원 조회</button></div><p id="r-source" class="muted">검증된 로컬 629개 · 인증 API 전체 갱신 미완료 · 최신성 미확인</p></section>
    <div class="roa-layout"><section class="panel"><h2>관련 복지자원</h2><p id="r-counts" role="status"></p><p class="muted">현재 이용 가능 확정 0건. 날짜가 맞아도 자격·운영·정원·접수 확인이 필요합니다.</p><div id="r-resources" class="roa-resources"></div><div class="actions"><button id="r-prev" type="button">이전</button><button id="r-next" type="button">다음</button></div></section>
    <section class="panel"><div class="section-heading"><h2>담당자 점검 Agent</h2><span id="r-agent-mode" class="tag">실제 데이터 정형 응답</span></div><p id="r-agent-context">행정동을 선택하세요.</p><div class="suggestions"><button type="button" data-r-question="유형이 왜 이렇게 나왔어?">유형 근거</button><button type="button" data-r-question="연결할 수 있는 복지자원을 찾아줘.">자원 검색</button><button type="button" data-r-question="활동 비교 방향이 다른데 왜 A가 OFF야?">A 비교 해석</button><button type="button" data-r-question="현재 자료로 확인하지 못한 부분과 정책 검토안을 알려줘.">공백·정책 검토</button></div><div id="r-messages" class="roa-chat" role="log" aria-live="polite"></div><form id="r-agent-form"><label>응답 모드<select id="r-engine"><option value="structured">실제 데이터 도구 · 정형 응답</option><option value="llm">LLM 도구 선택 · 설정 확인 중</option></select></label><label>선택 동에 관한 질문<textarea id="r-question" maxlength="1000" placeholder="선택한 동의 유형·자원·확인할 사항을 질문하세요."></textarea></label><button id="r-send" type="submit">질문 보내기</button></form><p id="r-agent-state" class="muted" role="status"></p><p class="muted">정형 모드는 질문 키워드·인용 검색어에 따라 실제 도구를 호출합니다. LLM 모드는 별도 활성화가 필요합니다. 근거에 없는 수치·사업명은 생성하지 않습니다. 지역·시점 변경 시 대화를 초기화합니다.</p></section></div>`;
  async function request(path, q = {}, payload) {
    const response = await fetch(path + (payload ? '' : '?' + new URLSearchParams(q)), payload ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)} : {});
    const data = await response.json();
    if (!response.ok) throw Error(data.error?.message || '자료 조회 실패');
    return data;
  }
  const context = () => ({dong:selectedCode, mode:el('r-mode').value, as_of:el('r-date').value});
  const links = values => (values || []).filter(x => /^https?:\/\//.test(x)).map(x => `<a href="${safe(x)}" target="_blank" rel="noopener noreferrer">원문·출처</a>`).join(' · ');
  const periodText = p => `${day(p.start)} ~ ${p.end || (p.open_end ? '종료일 미확인(열린 기간 기록)' : '미확인')}`;
  const labels = {CURRENT_OPERATION_UNVERIFIED:'현재 운영 확인',RECRUITMENT_UNVERIFIED:'모집·접수 확인',REMAINING_CAPACITY_UNVERIFIED:'잔여 정원 확인',AGE_REQUIRED:'대상 연령 확인',AGE_CONDITIONS_UNVERIFIED:'연령 조건 확인',GENDER_REQUIRED:'성별 조건 확인',GENDER_CONDITIONS_UNVERIFIED:'성별 조건 확인',SINGLE_HOUSEHOLD_REQUIRED:'1인가구 조건 확인',FULL_ELIGIBILITY_TEXT_REVIEW_REQUIRED:'원문 자격·선정조건 확인',RESIDENT_ELIGIBILITY_SCOPE_UNVERIFIED:'거주·생활권 자격 확인',OPERATING_DATES_UNKNOWN:'운영기간 확인',PILOT_OR_DETAILS_UNVERIFIED:'시범/세부 운영 확인',ACTUAL_HISTORICAL_PARTICIPATION_AND_EFFECT_UNKNOWN:'당시 실제 참여·효과 미확인',ENDED_BEFORE_PERIOD:'운영기간이 조회기간 이전',STARTS_AFTER_PERIOD:'운영기간이 조회기간 이후',PLAN_NOT_OPERATING_RESOURCE:'계획 단계',NOT_A_RESIDENT_APPLICATION_SERVICE:'주민 신청형 사업 아님',FUTURE_PROGRAM_WITH_RECORDED_APPLICATION_WINDOW:'운영은 미래 · 현재 접수 일정 기록',CURATED_EXCLUSION_ADULT_LINKAGE:'성인 연계 대상에서 제외',GANGNAM_EXCLUDED_AS_REPORTED_RECHECK_CURRENT:'보고 시점 강남 미적용 · 현재 재확인',RESIDENT_REGION_MISMATCH:'거주지 조건 불일치',AGE_MISMATCH:'연령 조건 불일치'};
  const reasonText = values => [...new Set(values.map(x => labels[x] || (x.startsWith('APPLICATION_') ? '접수 일정·방식 확인' : '누락 정보·원문 조건 확인')))].join(' · ');
  function clearChat() {
    agentRequest++;
    el('r-messages').replaceChildren(); el('r-question').value='';
    el('r-agent-mode').textContent=el('r-engine').value==='llm'?'LLM 도구 모드':'실제 데이터 정형 응답';
    el('r-agent-state').textContent=provider.message || '실제 데이터 도구를 사용하는 정형 응답';
    el('r-send').disabled=!selectedCode;
  }
  function showEvidence(d) {
    const c=d.classification;
    el('r-selected').textContent=`${d.region.adm_nm} · ${c.type || 'HOLD'}`;
    el('r-agent-context').textContent=`${d.region.adm_nm} · ${c.type || 'HOLD'} · 종합기간 2025년 10~12월`;
    const relationship = c.status==='HOLD' ? `<div class="notice">판정 보류: R/O는 판정 불가이며 실제 0이나 OFF로 해석하지 않습니다.<p>${c.hold_reasons.map(r=>safe(r.detail || r.code)).join(' / ')}</p></div>` : ['R','O'].map(axis=>`<h3>${axis==='R'?'R · 연락 관련 추정':'O · 평일 외출 관련 추정'} · ${safe(c.signals[axis].state)}</h3><div class="tablewrap"><table><tr><th>기준월</th><th>표준화 비율</th><th>21동 중앙값</th><th>초과</th></tr>${c.signals[axis].monthly_evidence.map(r=>`<tr><td>${safe(r.reference_month)}</td><td>${num(r.standardized_pct)}%</td><td>${num(r.median_21_pct)}%</td><td>${r.above_median===null?'미확인':r.above_median?'예':'아니오'}</td></tr>`).join('')}</table></div><p class="muted">세 기준월 중 ${c.signals[axis].above_months.length}/3개월 초과. OFF는 세 월 전체 상대기준 미충족.</p>`).join('');
    const activity=Object.entries(c.signals.A.comparisons).map(([key,r])=>`<tr><td>${key==='window_comparison'?'9~11월 / 7~9월':'11월 / 7월'}</td><td>${num(r.base_level)}</td><td>${num(r.target_level)}</td><td>${num(r.change_pct)}%</td><td>${r.decreased===null?'미확인':r.decreased?'충족':'미충족'}</td></tr>`).join('');
    el('r-evidence').innerHTML=`<p>${safe(c.display_name)}</p><div class="roa-signals">${['R','O','A'].map(s=>`<b>${s} ${safe(c.signal_composition[s])}</b>`).join('')}</div><p class="muted">2025년 10~12월 기준월 종합 · ${safe(c.rule_version)}</p>${relationship}<h3>A · 지역 활동 두 비교</h3><div class="tablewrap"><table><tr><th>비교</th><th>기준 수준</th><th>비교 수준</th><th>변화율</th><th>감소조건</th></tr>${activity}</table></div><p class="muted">A ON은 두 조건 충족이며 매월 연속 감소를 뜻하지 않습니다. 두 비교는 독립 검증이 아닙니다. 지역 활동은 주민 개인의 외출이 아닙니다.</p><details><summary>7~12월 활동 수준과 추가 근거</summary><table><tr><th>월</th><th>활동 수준</th><th>7월 대비</th><th>전월 대비</th><th>연속 감소 구간</th></tr>${d.sk_monthly_detail.map(r=>`<tr><td>${safe(r.observation_month)}</td><td>${num(r.activity_level)}</td><td>${num(r.vs_july_change_pct)}%</td><td>${num(r.vs_previous_month_change_pct)}%</td><td>${num(r.current_month_on_month_decrease_streak)}</td></tr>`).join('')}</table></details><details><summary>추가 질문·지원 검토 방향·해석 한계</summary>${[...c.additional_questions,...c.support_review_directions].map(x=>`<p>${safe(x.text)}</p>`).join('')}${c.caveats.map(x=>`<p class="muted">${safe(x)}</p>`).join('')}</details>`;
  }
  async function select(code) {
    if (el('r-city').value!=='11230') return;
    selectedCode=code; el('r-dong').value=code; generation++; resourceRequest++; offset=0;
    const ticket=generation; clearChat();
    el('r-evidence').textContent='동별 근거 조회 중…'; el('r-resources').textContent='자원 조회 중…';
    el('r-agent-context').textContent='선택 동 근거 확인 중…';
    if(group) group.eachLayer(l=>l.setStyle({weight:l.feature.properties.DONG_CODE===code?4:1.5}));
    try { const d=await request('/api/roa/detail',{dong:code}); if(ticket!==generation)return; showEvidence(d); await loadResources(); }
    catch(e){if(ticket===generation){el('r-evidence').textContent=e.message;el('r-resources').textContent='선택 동 근거 조회 실패';}}
  }
  function card(r) {
    const review=r.source_review?`<p class="notice">원문 대조 ${safe(r.source_review.checked_on)}: ${safe(r.source_review.note)} ${links([r.source_review.url])}</p>`:'';
    return `<article class="roa-resource"><h4>${safe(r.title)}</h4>${review}<p>${safe(r.provider)} · ${safe(r.assessment.bucket_label)}${r.curated?' · 선별 자료':''}</p><p>대상: ${safe(r.eligibility.target_text || '상세 자격 미확인')} / 연령 ${safe(r.eligibility.age_text || '미확인')} / 성별 ${safe(r.eligibility.gender_text || '미확인')}</p><p>운영: ${safe(periodText(r.operating_period))}<br>신청: ${safe(periodText(r.application_period))}</p><p>기관 관할: ${safe(r.geography.provider_jurisdiction || '미확인')} / 주민 자격: ${safe(r.eligibility.scope_evidence || '미확인')}<br>장소: ${safe(r.geography.venue || '미확인')}</p><p>${safe(reasonText([...r.assessment.exclusion_reasons,...r.assessment.checks_required]))}</p><p>${links(r.source_urls)} · 수집일 ${safe(r.retrieved_at || '미확인')} · 원문 최종 확인일 ${safe(r.source_checked_at || '미확인')} · 운영 확인 미완료</p><button type="button" data-resource-id="${safe(r.id)}">원문 조건 상세</button><div class="resource-detail" hidden></div></article>`;
  }
  async function loadResources() {
    if(!selectedCode)return;
    const ticket=++resourceRequest, gen=generation;
    el('r-counts').textContent='검색 중…'; el('r-resources').replaceChildren();
    try {
      const data=await request('/api/roa/welfare',{...context(),query:el('r-query').value,curated_only:el('r-curated').value,limit:5,offset});
      if(ticket!==resourceRequest||gen!==generation)return;
      total=Math.max(...Object.values(data.counts));
      resourceApi=data.data_status.source==='API_PLUS_LOCAL';
      el('r-counts').textContent=`연계 검토 후보 ${data.counts.candidate} · 자격·운영 확인 필요 ${data.counts.needs_confirmation} · 적용 제외 ${data.counts.excluded}`;
      const connection=externalStatus.welfare?.connection_diagnostic;
      const apiState=data.data_status.api_collection_complete?'공식 API 전체 수집 검증됨 · 현재 운영 별도 확인':connection?.status==='BLOCKED_NO_VERIFIED_HTTPS'?'서울시 HTTPS 연결 미확보 · 인증 전체 조회 차단':'인증 API 전체 갱신 미완료';
      el('r-source').textContent=`${data.data_status.source==='API_PLUS_LOCAL'?'API 자료와 로컬 자료 결합':'검증된 로컬 629개'} · ${apiState} · 현재 운영·자격·정원 미확인 · ${data.mode==='historical'?'2025년 7~11월 운영기간 겹침 검토':data.as_of+' 기준 운영/신청 일정 검토'}`;
      const html=['candidate','needs_confirmation'].map(k=>`<h3 style="font-size:14px">${k==='candidate'?'연계 검토 후보':'자격·운영 확인 필요'}</h3>${data.results[k].map(card).join('')||'<p class="muted">해당 조건의 결과가 없습니다. 서비스 부재를 뜻하지 않습니다.</p>'}`).join('');
      el('r-resources').innerHTML=html+`<details><summary>적용 제외 ${data.counts.excluded}개 · 사유 확인</summary>${data.results.excluded.map(card).join('')}</details>`;
      if(data.support_search_status!=='RO_CANDIDATE_REVIEW')el('r-resources').prepend(Object.assign(document.createElement('p'),{textContent:data.support_search_status==='HOLD_NO_RO_INFERENCE'?'판정 보류: R/O 지원 방향 자동 추론 없음.':'R/O 전체기간 기준 미충족: A만으로 직접 복지지원 필요를 추론하지 않습니다.'}));
      el('r-prev').disabled=offset===0; el('r-next').disabled=offset+5>=total;
    }catch(e){if(ticket===resourceRequest){el('r-counts').textContent=e.message;el('r-prev').disabled=true;el('r-next').disabled=true;}}
  }
  el('r-resources').addEventListener('click',async event=>{
    const button=event.target.closest('[data-resource-id]'); if(!button)return;
    const target=button.nextElementSibling; target.hidden=!target.hidden; if(target.hidden||target.dataset.loaded)return;
    try{const r=await request('/api/roa/resource',{id:button.dataset.resourceId,use_api_cache:String(resourceApi)});target.textContent=[r.content,`신청방법: ${r.application.method||'미확인'}`,`문의: ${r.application.contact||'미확인'}`,`게시 참여인원: ${r.application.capacity_text||'미확인'} (잔여 정원 아님)`,`요금: ${r.application.fee_text||'미확인'}`].join('\n');target.style.whiteSpace='pre-wrap';target.dataset.loaded='true';}catch(e){target.textContent=e.message;}
  });
  async function setCity(city, sync=true) {
    const supported=city==='11230'; el('r-city').value=supported?'11230':'32010';
    generation++; resourceRequest++; selectedCode=''; clearChat();
    el('r-map-box').hidden=!supported; el('r-unavailable').hidden=supported; el('r-dong').disabled=!supported;
    el('r-resources').textContent=supported?'행정동을 선택하세요.':'춘천 관계망·ROA 자원 연결은 제공하지 않습니다.';
    el('r-counts').textContent=''; el('r-agent-context').textContent=supported?'행정동을 선택하세요.':'춘천: SK 상세 탐색만 제공';
    el('r-status').textContent=supported?'21동 판정 · 1동 HOLD':'춘천 관계망·ROA 미적용';
    el('r-legend').hidden=!supported; el('r-prev').disabled=true;el('r-next').disabled=true;
    if(sync&&el('city')?.options.length){const sk=el('city');const match=[...sk.options].find(x=>x.text.includes(supported?'강남':'춘천'));if(match&&sk.value!==match.value){sk.value=match.value;sk.dispatchEvent(new Event('change'));}}
    if(!supported){el('sk-explore').open=true;map.invalidateSize();return;}
    roaMap.invalidateSize(); await select(catalog.dongs[0].adm_cd8);
  }
  el('r-agent-form').onsubmit=async event=>{
    event.preventDefault(); if(!selectedCode)return;
    const question=el('r-question').value.trim(); if(!question){el('r-agent-state').textContent='질문을 입력하세요.';return;}
    const gen=generation, ticket=++agentRequest; el('r-send').disabled=true;el('r-agent-state').textContent='선택 동의 실제 근거·자원 조회 중…';
    try {
      const result=await request('/api/agent',{}, {...context(),question,engine:el('r-engine').value,query:el('r-query').value||undefined,curated_only:el('r-curated').value==='true'});
      if(gen!==generation||ticket!==agentRequest)return;
      provider=result.provider_status;
      el('r-agent-mode').textContent=result.agent_mode==='LLM_TOOL_SELECTION_GROUNDED_RENDERING'?'실제 LLM + 검증값 응답':result.llm_failure?'정형 대체 모드':'정형 응답 모드';
      const article=document.createElement('article');
      const q=document.createElement('p');q.className='chat-question';q.textContent=question;article.append(q);
      const label=document.createElement('p');label.textContent=`${result.region.adm_nm} · ${result.type||'HOLD'} · ${result.agent_mode==='LLM_TOOL_SELECTION_GROUNDED_RENDERING'?'LLM 도구 선택 + 검증값 응답':result.llm_failure?'LLM 사용 불가 · 정형 대체':'실제 데이터 정형 응답'}`;article.append(label);
      for(const section of result.sections){const h=document.createElement('h3');h.textContent=section.title;article.append(h);const list=document.createElement('ul');for(const line of section.lines){const li=document.createElement('li');li.textContent=line;list.append(li);}article.append(list);}
      for(const bucket of ['candidate','needs_confirmation'])for(const r of result.resources.results[bucket].slice(0,3)){const p=document.createElement('p');p.innerHTML=`${safe(r.title)} · ${links(r.source_urls)}`;article.append(p);}
      const trace=document.createElement('p');trace.className='muted';trace.textContent='조회 도구: '+result.tool_trace.map(x=>x.name).join(' → ');article.append(trace);
      el('r-messages').append(article);article.scrollIntoView({block:'nearest'});el('r-question').value='';el('r-agent-state').textContent=result.llm_failure?'LLM 미사용/실패 · 검증된 데이터 정형 응답으로 대체했습니다.':result.agent_mode==='LLM_TOOL_SELECTION_GROUNDED_RENDERING'?`${provider.provider} · ${provider.last_success?.response_model||provider.model} 실제 도구 호출 완료`:'선택 동의 검증된 수치·검색 결과를 표시했습니다.';
    }catch(e){if(gen===generation&&ticket===agentRequest)el('r-agent-state').textContent=e.message;}
    finally{if(gen===generation&&ticket===agentRequest)el('r-send').disabled=false;}
  };
  document.querySelectorAll('[data-r-question]').forEach(button=>button.onclick=()=>{const d=catalog.dongs.find(d=>d.adm_cd8===selectedCode);el('r-question').value=(d?d.adm_nm+'의 ':'')+button.dataset.rQuestion;el('r-question').focus();});
  el('r-dong').onchange=()=>select(el('r-dong').value);
  el('r-city').onchange=()=>setCity(el('r-city').value);
  el('r-search').onclick=()=>{offset=0;clearChat();loadResources();};
  el('r-query').oninput=()=>{clearChat();resourceRequest++;offset=0;el('r-resources').replaceChildren();el('r-counts').textContent='검색 조건이 변경되었습니다. 자원 조회를 눌러 주세요.';el('r-prev').disabled=true;el('r-next').disabled=true;};
  // 자원 시점/범위는 동별 근거 요청을 무효화하지 않는다. 각 자원 요청은 resourceRequest로 구분한다.
  for(const id of ['r-mode','r-date','r-curated'])el(id).onchange=()=>{el('r-date').disabled=el('r-mode').value==='historical';clearChat();offset=0;loadResources();};
  el('r-prev').onclick=()=>{offset=Math.max(0,offset-5);loadResources();};el('r-next').onclick=()=>{offset+=5;loadResources();};
  el('sk-explore').addEventListener('toggle',()=>{if(el('sk-explore').open){map.invalidateSize();document.querySelectorAll('.plot').forEach(p=>Plotly.Plots.resize(p));}});
  async function initRoa(){
    try{
      [catalog,geography,provider,externalStatus]=await Promise.all([request('/api/roa/catalog'),request('/api/roa/map'),request('/api/agent/status'),request('/api/service/status')]);
      el('r-dong').replaceChildren(...catalog.dongs.map(d=>new Option(`${d.adm_nm} · ${d.type||'HOLD'}`,d.adm_cd8)));
      el('r-date').value=catalog.as_of_default;
      el('r-legend').innerHTML=catalog.legend.map(l=>`<span><i style="background:${safe(l.color)};border:2px ${l.a_on?'dashed':'solid'} #2c3443"></i>${safe(l.type)} ${l.count}동</span>`).join('');
      roaMap=L.map('r-map',{preferCanvas:true}).setView([37.51,127.06],12);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap contributors'}).addTo(roaMap);
      group=L.geoJSON(geography,{style:f=>({fillColor:f.properties.color,color:f.properties.status==='HOLD'?'#59616c':f.properties.color,weight:1.5,fillOpacity:.27,dashArray:f.properties.status==='HOLD'?'3 4':f.properties.a_state==='ON'?'7 4':null}),onEachFeature:(f,l)=>{l.bindTooltip(`${safe(f.properties.NAME)} · ${safe(f.properties.type||'HOLD')}`);l.on('click',()=>select(f.properties.DONG_CODE));}}).addTo(roaMap);
      roaMap.fitBounds(group.getBounds());
      const option=el('r-engine').options[1];option.disabled=!provider.ready;option.textContent=provider.ready?`${provider.provider} · ${provider.model} 실제 도구 호출`:`${provider.provider} · 설정/연결 필요`;
      el('r-engine').value=provider.ready?'llm':'structured';
      el('r-agent-mode').textContent=provider.ready?'실제 LLM 도구 모드':'실제 데이터 정형 응답';
      window.roaSelectFromSK=(code,city)=>{if(city==='11230'&&el('r-city').value==='11230'&&code!==selectedCode)select(code);};
      window.roaCityFromSK=city=>{const value=city==='11230'?'11230':'32010';if(el('r-city').value!==value)setCity(value,false);};
      await setCity('11230',false);
    }catch(e){el('r-status').textContent=e.message;el('r-agent-state').textContent='통합 데이터 로딩 실패';}
  }
  initRoa();
})();
