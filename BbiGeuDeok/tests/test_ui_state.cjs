/* Synthetic UI contract tests: no browser, credentials, region observations or network. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const work=path.resolve(__dirname,'../Code/sk_admin_mapping');
class Element {
  constructor(id=''){this.id=id;this.value='';this.textContent='';this.innerHTML='';this.hidden=false;this.disabled=false;this.children=[];this.options=[{},{}];this.events={};this.dataset={};this.attributes={};this.style={};this.classList={add(){},remove(){},toggle(){return true;}};}
  addEventListener(type,fn){(this.events[type]??=[]).push(fn);}
  emit(type){for(const fn of this.events[type]||[])fn({preventDefault(){}});}
  setAttribute(k,v){this.attributes[k]=v;} removeAttribute(k){delete this.attributes[k];}
  replaceChildren(...children){this.children=children;this.innerHTML='';} append(...children){this.children.push(...children);}
  insertAdjacentHTML(position,html){this.innerHTML=position==='afterbegin'?html+this.innerHTML:this.innerHTML+html;}
  focus(){} remove(){}
}
function environment(source='dashboard.js',extra={}){
  const nodes=new Map(),listeners={},windowEvents={},storage=new Map();
  const node=id=>{if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id);};
  const pages=['overview','signals','activity','welfare','agent'].map(x=>node('page-'+x));
  const nav=pages.map(p=>{let n=new Element();n.dataset.page=p.id.slice(5);return n;});
  const doc={getElementById:node,querySelector:s=>node(s),querySelectorAll:s=>s==='.page'?pages:s==='[data-page]'?nav:[],addEventListener:(type,fn)=>(listeners[type]??=[]).push(fn),dispatchEvent:e=>{for(const fn of listeners[e.type]||[])fn(e);},createElement:()=>new Element(),head:node('head')};
  let url={hash:'#/overview'};
  const scope={document:doc,location:url,history:{replaceState(_a,_b,h){url.hash=h;}},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},setTimeout,clearTimeout,AbortController,URLSearchParams,Event:class{constructor(type){this.type=type;}},CustomEvent:class{constructor(type,o){this.type=type;this.detail=o.detail;}},requestAnimationFrame:f=>{f();return 1;},cancelAnimationFrame(){},Option:class{constructor(t,v){this.text=t;this.value=v;}},fetch:async()=>({ok:true,json:async()=>({dongs:[]})}),scrollTo(){},addEventListener:(type,fn)=>windowEvents[type]=fn,...extra};
  scope.window=scope;vm.createContext(scope);vm.runInContext(fs.readFileSync(path.join(work,source),'utf8'),scope);
  return {scope,nodes,node,doc,storage,windowEvents,listeners,pages};
}
test('shared city switch clears dong and preserves only context in session',()=>{const e=environment();e.scope.Dashboard.setContext({dong:'00000001'});e.scope.Dashboard.setContext({city:'32010'});assert.equal(e.scope.Dashboard.context.dong,'');assert.equal(JSON.parse(e.storage.get('bbigeudeok.context.v1')).city,'32010');});
test('unsupported city cannot enter shared state',()=>{const e=environment();e.scope.Dashboard.setContext({city:'bad'});assert.equal(e.scope.Dashboard.context.city,'11230');});
test('same context causes no extra subscriber work',()=>{const e=environment();let calls=0;e.scope.Dashboard.register('signals',{contextChanged(){calls++;}});e.scope.Dashboard.setContext({city:'11230',dong:''});assert.equal(calls,0);});
test('all controllers observe one shared dong change',()=>{const e=environment();let values=[];for(const name of ['signals','activity','welfare'])e.scope.Dashboard.register(name,{contextChanged(c){values.push(c.dong);}});e.scope.Dashboard.setContext({dong:'00000002'});assert.deepEqual(values,['00000002','00000002','00000002']);});
test('route enters only requested controller and never preloads hidden pages',async()=>{const e=environment();let calls=[];for(const name of ['overview','signals','activity','welfare','agent'])e.scope.Dashboard.register(name,{enter(){calls.push(name);}});assert.deepEqual(calls,[]);e.scope.location.hash='#/welfare';await e.windowEvents.hashchange();assert.deepEqual(calls,['welfare']);assert.equal(e.node('page-welfare').hidden,false);assert.equal(e.node('page-activity').hidden,true);});
test('invalid hash normalizes to overview',async()=>{const e=environment();e.scope.location.hash='#/not-a-page';await e.windowEvents.hashchange();assert.equal(e.scope.location.hash,'#/overview');});
test('failed memoized request can be retried; successful requests are shared',async()=>{const e=environment();let calls=0;await assert.rejects(e.scope.Dashboard.cached('key',async()=>{calls++;throw Error('offline');}));assert.equal(await e.scope.Dashboard.cached('key',async()=>{calls++;return 7;}),7);assert.equal(await e.scope.Dashboard.cached('key',async()=>99),7);assert.equal(calls,2);});
test('failed chart script can be retried without a poisoned promise',async()=>{const e=environment();const first=e.scope.Dashboard.loadScript('/plotly.js');e.node('head').children[0].onerror();await assert.rejects(first);const next=e.scope.Dashboard.loadScript('/plotly.js');e.node('head').children[1].onload();await next;});
function agentEnvironment(){let resolve,reject;const requests=[],controllers={};const d={context:{city:'11230',dong:'00000001'},page:'overview',safe:v=>String(v??''),request:(_p,_q,payload)=>{requests.push(payload);return new Promise((r,j)=>{resolve=r;reject=j;});},register:(n,c)=>controllers[n]=c,roaCatalog:async()=>({as_of_default:'2026-01-01'})};const e=environment('roa_dashboard.js',{Dashboard:d});for(const [id,value] of Object.entries({'a-mode':'current','a-date':'2026-01-01','r-engine':'structured','a-query':'explicit query','a-curated':'true','r-query':'different welfare query','r-question':'synthetic question'}))e.node(id).value=value;return {...e,requests,controllers,finish:x=>resolve(x),fail:x=>reject(x),submit:()=>e.node('r-agent-form').onsubmit({preventDefault(){}})};}
const reply={provider_status:{},agent_mode:'STRUCTURED',region:{adm_nm:'synthetic region'},type:'NONE_A0',sections:[{title:'synthetic evidence',lines:['synthetic fact']}],resources:{results:{candidate:[],needs_confirmation:[]}},tool_trace:[]};
test('Agent sends exact question and its own query scope',async()=>{const e=agentEnvironment();const p=e.submit();assert.equal(e.requests[0].question,'synthetic question');assert.equal(e.requests[0].query,'explicit query');assert.equal(e.requests[0].curated_only,true);e.finish(reply);await p;assert.equal(e.node('r-messages').children.length,1);});
test('pending Agent rejects duplicate sends including after question edits',async()=>{const e=agentEnvironment();const p=e.submit();await e.submit();e.node('r-question').value='edited question';e.node('r-question').emit('input');await e.submit();assert.equal(e.requests.length,1);assert.equal(e.node('r-send').disabled,true);e.finish(reply);await p;assert.equal(e.node('r-messages').children.length,0);assert.equal(e.node('r-send').disabled,false);});
test('context change discards late Agent answer and clears draft',async()=>{const e=agentEnvironment();const p=e.submit();e.scope.Dashboard.context={city:'11230',dong:'00000002'};e.controllers.signals.contextChanged();e.finish(reply);await p;assert.equal(e.node('r-messages').children.length,0);assert.equal(e.node('r-question').value,'');});
test('welfare scope invalidation discards Agent answer',async()=>{const e=agentEnvironment();const p=e.submit();e.doc.dispatchEvent({type:'dashboard:invalidate-agent'});e.finish(reply);await p;assert.equal(e.node('r-messages').children.length,0);});
test('Agent failure releases send button and removes response evidence',async()=>{const e=agentEnvironment();const p=e.submit();e.fail(Error('synthetic API failure'));await p;assert.equal(e.node('r-send').disabled,false);assert.match(e.node('r-agent-state').textContent,/synthetic API failure/);assert.match(e.node('a-sources').textContent,/요청 실패/);});

// Independent display contracts: synthetic values deliberately round to the same text.
function evidenceEnvironment(hold=false){
  const controllers={};const monthly=[1,2,3].map(i=>({reference_month:'20000'+i,standardized_pct:10.124,median_21_pct:10.123,above_median:true}));
  const d={region:{adm_nm:'synthetic region'},classification:{type:hold?null:'R_A0',status:hold?'HOLD':'CLASSIFIED',display_name:'synthetic classification',rule_version:'synthetic.v1',signal_composition:{R:hold?'UNDETERMINED':'ON',O:hold?'UNDETERMINED':'OFF',A:'OFF'},hold_reasons:[{detail:'synthetic missing evidence'}],signals:{R:{state:'ON',monthly_evidence:monthly,above_months:[1,2,3]},O:{state:'OFF',monthly_evidence:monthly,above_months:[1,2]},A:{comparisons:{endpoint_comparison:{base_level:20.1234,target_level:20.1235,change_pct:0.0005,decreased:false}}}},additional_questions:[],support_review_directions:[],caveats:[]},sk_monthly_detail:[]};
  const original=JSON.stringify(d);const api={context:{city:'11230',dong:'00000001'},page:'agent',safe:v=>String(v??''),request:async()=>({ready:false}),register:(n,c)=>controllers[n]=c,roaCatalog:async()=>({as_of_default:'2000-01-01'}),detail:async()=>d};
  return {...environment('roa_dashboard.js',{Dashboard:api}),controllers,data:d,original};
}
test('rounded equal evidence retains original precision and server comparison without mutating input',async()=>{const e=evidenceEnvironment();await e.controllers.agent.enter();const html=e.node('r-evidence').innerHTML;assert.match(html,/10\.12<\/span>%/);assert.doesNotMatch(html,/>10\.124<\/span>/);assert.match(html,/원래 값: 10\.124/);assert.match(html,/원래 값: 10\.123/);assert.match(html,/<td>예<\/td>/);assert.equal(JSON.stringify(e.data),e.original);});
test('HOLD labels never turn undetermined relationship signals into OFF',async()=>{const e=evidenceEnvironment(true);await e.controllers.agent.enter();assert.match(e.node('a-evidence').innerHTML,/R HOLD/);assert.match(e.node('a-evidence').innerHTML,/data-state="UNDETERMINED"/);assert.match(e.node('r-evidence').innerHTML,/실제 0이나 OFF로 해석하지 않습니다/);});
function welfareEnvironment(status){const controllers={};const api={context:{city:'11230',dong:'00000001'},page:'welfare',safe:v=>String(v??''),request:async()=>({data_status:status,counts:{candidate:0,needs_confirmation:0,excluded:0},results:{candidate:[],needs_confirmation:[],excluded:[]},mode:'current',as_of:'2000-01-01',support_search_status:'RO_CANDIDATE_REVIEW'}),register:(n,c)=>controllers[n]=c,invalidateAgent(){},roaCatalog:async()=>({as_of_default:'2000-01-01'})};return {...environment('welfare_explorer.js',{Dashboard:api}),controllers};}
test('resource collection status distinguishes missing detail, conflicts and confirmed availability',async()=>{const e=welfareEnvironment({source:'API_PLUS_LOCAL',api_status:{service_join:{list_without_detail_ids:['synthetic-missing']}},id_attribute_conflicts_local_kept:['synthetic-a','synthetic-b'],currently_available_confirmed_count:0});await e.controllers.welfare.enter();assert.match(e.node('r-data-status').textContent,/상세 누락 1건/);assert.match(e.node('r-data-status').textContent,/속성 충돌 2건/);assert.match(e.node('r-data-status').textContent,/이용 가능 확정 0건/);assert.equal(e.node('r-page').textContent,'조회 결과 없음');});
test('local-only resource status reports unknown API detail completeness rather than zero',async()=>{const e=welfareEnvironment({source:'LOCAL_CATALOG'});await e.controllers.welfare.enter();assert.match(e.node('r-data-status').textContent,/상세 누락 미확인/);assert.doesNotMatch(e.node('r-data-status').textContent,/상세 누락 0건/);});

test('protected demo login redirect becomes an explicit notice without replaying Agent POST',async()=>{
  const calls=[];const origin='https://synthetic-demo.trycloudflare.com';
  const e=environment('dashboard.js',{location:{protocol:'https:',hostname:'synthetic-demo.trycloudflare.com',origin,pathname:'/',hash:'#/agent'},fetch:async(url,options)=>{calls.push({url,options});return {type:'opaqueredirect',status:0};}});
  const original=e.doc.getElementById;e.doc.getElementById=id=>id==='demo-auth-required'?(e.nodes.get(id)||null):original(id);
  const banners=[];e.doc.body={prepend:element=>{banners.push(element);e.nodes.set(element.id,element);}};
  await assert.rejects(e.scope.Dashboard.request('/api/agent',{}, {engine:'llm',question:'SYNTHETIC_PRIVATE_QUESTION'}),/접속 인증을 갱신/);
  assert.equal(calls.length,1);assert.equal(calls[0].options.method,'POST');assert.equal(calls[0].options.redirect,'manual');
  await assert.rejects(e.scope.Dashboard.request('/api/catalog'),/접속 인증을 갱신/);assert.equal(banners.length,1);
  assert.doesNotMatch(banners[0].children[0].textContent,/SYNTHETIC_PRIVATE_QUESTION/);
  assert.equal(banners[0].children[1].href,origin+'/#/agent');
});
test('local requests and successful protected demo requests retain existing JSON behavior',async()=>{
  for(const remote of [false,true]){const calls=[];const location=remote?{protocol:'https:',hostname:'synthetic-demo.trycloudflare.com'}:{protocol:'http:',hostname:'127.0.0.1'};
    const e=environment('dashboard.js',{location,fetch:async(url,options)=>{calls.push(options);return {type:'basic',status:200,ok:true,json:async()=>({synthetic:true})};}});
    assert.equal((await e.scope.Dashboard.request('/api/catalog')).synthetic,true);assert.equal(calls.length,1);assert.equal(calls[0].redirect,remote?'manual':undefined);
  }
});


test('quota failures show readable messages for local and GCP dashboards',async()=>{
  for(const source of ['dashboard.js','../../infra/gcp/dashboard.js']){
    for(const [error,expected] of [
      [{code:'DEMO_LLM_HOURLY_LIMIT'},/시간당 질문 30회/],
      [{code:'DEMO_LLM_DAILY_LIMIT'},/하루 질문 100회/],
      [{code:'DEMO_LLM_GLOBAL_DAILY_LIMIT'},/전체 하루 질문 1,000회/],
      [{code:'DEMO_AGENT_BUSY'},/다른 질문에 응답 중/],
      [{code:'UNKNOWN_CODE'},/UNKNOWN_CODE/],
      [{message:'server message'},/server message/],
      ['legacy message',/legacy message/]
    ]){
      const e=environment(source,{fetch:async()=>({ok:false,status:429,json:async()=>({error})})});
      await assert.rejects(e.scope.Dashboard.request('/api/agent'),expected);
    }
  }
});
