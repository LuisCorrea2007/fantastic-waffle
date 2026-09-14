const $ = (id) => document.getElementById(id);
const state = {
  setupRequired: false, user: null, health: null,
  envs: [], dbConnections: [], webProfiles: [], apiProfiles: [], mobileProfiles: [],
  currentDb: null, currentSchema: null, lastMemoryId: null,
  browserSession: null, mobileSession: null, currentCaseId: null, lastImportedSavedIds: [],
};

async function request(url, options={}) {
  const opts = {...options};
  opts.headers = {...(opts.headers||{})};
  if (opts.body && typeof opts.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch(url, opts);
  let data = null;
  const ct = r.headers.get('content-type') || '';
  if (ct.includes('application/json')) data = await r.json();
  else data = await r.text();
  if (r.status === 401 && !url.startsWith('/api/auth/')) {
    showAuth(false); throw new Error('Sesión expirada.');
  }
  if (!r.ok) throw new Error(data?.detail || (typeof data === 'string' ? data : `HTTP ${r.status}`));
  return data;
}
const post = (url, body) => request(url, {method:'POST', body});
async function requestForm(url, formData){ const r=await fetch(url,{method:'POST',body:formData}); let d=null; const ct=r.headers.get('content-type')||''; d=ct.includes('application/json')?await r.json():await r.text(); if(!r.ok) throw new Error(d?.detail || (typeof d==='string'?d:`HTTP ${r.status}`)); return d; }

function setGlobal(msg, kind='muted') {
  $('globalStatus').textContent = msg;
  $('globalStatus').className = `badge ${kind}`;
}
function escapeHtml(v) { return String(v??'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function parseJson(text, fallback={}) { if (!String(text||'').trim()) return fallback; try { return JSON.parse(text); } catch(e) { throw new Error(`JSON inválido: ${e.message}`); } }

function showAuth(setup) {
  state.setupRequired = setup;
  $('appShell').classList.add('hidden'); $('authScreen').classList.remove('hidden');
  $('authSubtitle').textContent = setup ? 'Primera ejecución: crea el administrador.' : 'Inicia sesión en el Control Center.';
  $('authBtn').textContent = setup ? 'Crear administrador' : 'Entrar';
}
function showApp(user) {
  state.user = user; $('authScreen').classList.add('hidden'); $('appShell').classList.remove('hidden');
  $('userBadge').textContent = user?.username || 'admin';
}

async function bootstrap() {
  const auth = await request('/api/auth/status');
  if (auth.setup_required || !auth.authenticated) { showAuth(auth.setup_required); return; }
  showApp(auth.user); await loadCore();
}

$('authBtn').onclick = async () => {
  $('authMessage').textContent='';
  try {
    const url = state.setupRequired ? '/api/auth/setup' : '/api/auth/login';
    const data = await post(url,{username:$('authUser').value,password:$('authPassword').value});
    showApp(data.user); await loadCore();
  } catch(e) { $('authMessage').textContent=e.message; $('authMessage').className='message error'; }
};
$('logoutBtn').onclick = async () => { try{await post('/api/auth/logout',{});}finally{showAuth(false);} };

const pageMeta = {
  dashboard:['Dashboard','Estado general del entorno QA.'], workspace:['QA Workspace','Importa casos, genera planes y ejecuta QA Web/Mobile/API/DB con evidencia.'],
  connections:['Connections','Ambientes y conexiones cifradas.'], sql:['SQL Lab','PostgreSQL READ ONLY + IA + memoria persistente.'],
  api:['API Lab','Pruebas HTTP integradas.'], cases:['Test Cases','Planes deterministas generados o editados en JSON.'], executions:['Executions','Historial y reportes.'],
  learning:['AI & Learning','Memoria independiente del proveedor de IA.'], system:['Environment Check','Dependencias disponibles en el equipo.']
};
document.querySelectorAll('.nav').forEach(btn=>btn.onclick=async()=>{
  document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active')); btn.classList.add('active');
  document.querySelectorAll('.page').forEach(x=>x.classList.remove('active')); $(`page-${btn.dataset.page}`).classList.add('active');
  const m=pageMeta[btn.dataset.page]; $('pageTitle').textContent=m[0]; $('pageSubtitle').textContent=m[1];
  if(btn.dataset.page==='dashboard') await loadDashboard();
  if(btn.dataset.page==='executions') await loadExecutions();
  if(btn.dataset.page==='system') await runSystemCheck();
  if(btn.dataset.page==='learning') renderLearning();
});

async function loadCore() {
  state.health = await request('/api/health');
  $('aiStatus').textContent=`AI Router · ${state.health.ai_provider_count||0} proveedores`;
  $('maxRows').value=state.health.default_max_rows||200;
  await Promise.all([loadEnvs(), loadDbConnections(), loadWebProfiles(), loadApiProfiles(), loadMobileProfiles(), loadCases()]);
  await loadDashboard(); renderLearning();
}

async function loadDashboard(){
  const d=await request('/api/dashboard');
  const metrics=[['Ambientes',d.environments],['DB',d.db_connections],['Web',d.web_profiles],['APIs',d.api_profiles],['Mobile',d.mobile_profiles],['Casos',d.test_cases],['Ejecuciones',d.executions],['PASS',d.passed],['FAIL',d.failed],['BLOCKED',d.blocked]];
  $('metricGrid').innerHTML=metrics.map(([k,v])=>`<div class="metric"><strong>${v}</strong><span>${k}</span></div>`).join('');
  renderExecutions(d.recent_executions||[],$('recentExecutions'));
}
$('refreshDashboard').onclick=loadDashboard;

async function loadEnvs(){
  const d=await request('/api/environments'); state.envs=d.items||[];
  $('envList').innerHTML=state.envs.map(e=>`<div class="list-item"><div><strong>${escapeHtml(e.name)}</strong><br><small>${escapeHtml(e.description||'')}</small></div><button class="secondary env-del" data-id="${e.id}">Eliminar</button></div>`).join('')||'<div class="hint">Sin ambientes.</div>';
  document.querySelectorAll('.env-del').forEach(b=>b.onclick=async()=>{await request(`/api/environments/${b.dataset.id}`,{method:'DELETE'});await loadEnvs();});
  for(const id of ['webEnv','apiEnv','mobileEnv','caseEnv','importEnv']){
    const el=$(id), prev=el.value; el.innerHTML='<option value="">— Sin asignar —</option>'+state.envs.map(e=>`<option value="${e.id}">${escapeHtml(e.name)}</option>`).join(''); if(prev)el.value=prev;
  }
}
$('saveEnvBtn').onclick=async()=>{try{await post('/api/environments',{name:$('envName').value,description:$('envDescription').value,is_active:true});$('envName').value='';$('envDescription').value='';await loadEnvs();setGlobal('Ambiente guardado','good');}catch(e){setGlobal(e.message,'bad');}};

function dbForm(){return {host:$('dbHost').value,port:Number($('dbPort').value||5432),database:$('dbDatabase').value,user:$('dbUser').value,password:$('dbPassword').value,sslmode:$('dbSsl').value,schema_name:$('dbSchema').value||'public'};}
async function loadDbConnections(){
  const d=await request('/api/connections'); state.dbConnections=d.connections||[];
  $('dbList').innerHTML=state.dbConnections.map(c=>`<div class="list-item"><div><strong>${escapeHtml(c.name)}</strong><br><small>${escapeHtml(c.database_name)}@${escapeHtml(c.host)} · ${escapeHtml(c.schema_name)}</small></div><div class="row"><button class="secondary db-use" data-id="${c.id}">Usar</button><button class="secondary db-del" data-id="${c.id}">Eliminar</button></div></div>`).join('')||'<div class="hint">Sin conexiones guardadas.</div>';
  $('sqlConnection').innerHTML='<option value="">— Seleccionar DB —</option>'+state.dbConnections.map(c=>`<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
  $('importDb').innerHTML='<option value="">— Sin DB —</option>'+state.dbConnections.map(c=>`<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
  document.querySelectorAll('.db-use').forEach(b=>b.onclick=()=>{ $('sqlConnection').value=b.dataset.id; $('sqlConnection').dispatchEvent(new Event('change')); document.querySelector('[data-page="sql"]').click(); });
  document.querySelectorAll('.db-del').forEach(b=>b.onclick=async()=>{await request(`/api/connections/${b.dataset.id}`,{method:'DELETE'});await loadDbConnections();});
}
$('testDbBtn').onclick=async()=>{try{const d=await post('/api/test-connection',dbForm());setGlobal(`DB OK · ${d.elapsed_ms} ms`,'good');}catch(e){setGlobal(e.message,'bad');}};
$('saveDbBtn').onclick=async()=>{try{const d=await post('/api/connections',{name:$('dbName').value,db:dbForm()});setGlobal(`Conexión guardada · ${d.connection_ms} ms`,'good');await loadDbConnections();}catch(e){setGlobal(e.message,'bad');}};

async function loadWebProfiles(){
  const d=await request('/api/web-profiles');state.webProfiles=d.items||[];
  $('webList').innerHTML=state.webProfiles.map(p=>`<div class="list-item"><div><strong>${escapeHtml(p.name)}</strong><br><small>${escapeHtml(p.base_url)} · ${escapeHtml(p.browser)}</small></div><button class="secondary web-del" data-id="${p.id}">Eliminar</button></div>`).join('')||'<div class="hint">Sin perfiles Web.</div>';
  for(const id of ['workspaceWebProfile','importWeb']) $(id).innerHTML='<option value="">— Perfil Web —</option>'+state.webProfiles.map(p=>`<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
  document.querySelectorAll('.web-del').forEach(b=>b.onclick=async()=>{await request(`/api/web-profiles/${b.dataset.id}`,{method:'DELETE'});await loadWebProfiles();});
}
$('saveWebBtn').onclick=async()=>{try{await post('/api/web-profiles',{name:$('webName').value,environment_id:Number($('webEnv').value)||null,base_url:$('webBaseUrl').value,login_url:$('webLoginUrl').value,username:$('webUser').value,password:$('webPassword').value,browser:$('webBrowser').value,headless:$('webHeadless').value==='true',timeout_ms:30000});await loadWebProfiles();setGlobal('Perfil Web guardado','good');}catch(e){setGlobal(e.message,'bad');}};

async function loadApiProfiles(){
  const d=await request('/api/api-profiles');state.apiProfiles=d.items||[];
  $('apiProfileList').innerHTML=state.apiProfiles.map(p=>`<div class="list-item"><div><strong>${escapeHtml(p.name)}</strong><br><small>${escapeHtml(p.base_url)}</small></div><button class="secondary api-del" data-id="${p.id}">Eliminar</button></div>`).join('')||'<div class="hint">Sin perfiles API.</div>';
  $('apiLabProfile').innerHTML='<option value="">— Perfil API —</option>'+state.apiProfiles.map(p=>`<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
  $('importApi').innerHTML='<option value="">— Sin API —</option>'+state.apiProfiles.map(p=>`<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
  document.querySelectorAll('.api-del').forEach(b=>b.onclick=async()=>{await request(`/api/api-profiles/${b.dataset.id}`,{method:'DELETE'});await loadApiProfiles();});
}
$('saveApiBtn').onclick=async()=>{try{await post('/api/api-profiles',{name:$('apiName').value,environment_id:Number($('apiEnv').value)||null,base_url:$('apiBaseUrl').value,auth_header:$('apiAuthHeader').value,token:$('apiToken').value,verify_tls:$('apiVerifyTls').value==='true',timeout_ms:30000,default_headers:parseJson($('apiDefaultHeaders').value,{})});await loadApiProfiles();setGlobal('Perfil API guardado','good');}catch(e){setGlobal(e.message,'bad');}};


async function loadMobileProfiles(){
  const d=await request('/api/mobile-profiles');state.mobileProfiles=d.items||[];
  $('mobileList').innerHTML=state.mobileProfiles.map(p=>`<div class="list-item"><div><strong>${escapeHtml(p.name)}</strong><br><small>${escapeHtml(p.platform)} · ${escapeHtml(p.device_name||'')} · ${escapeHtml(p.appium_url||'')}</small></div><button class="secondary mobile-del" data-id="${p.id}">Eliminar</button></div>`).join('')||'<div class="hint">Sin perfiles Mobile.</div>';
  for(const id of ['workspaceMobileProfile','importMobile']) $(id).innerHTML='<option value="">— Perfil Mobile —</option>'+state.mobileProfiles.map(p=>`<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
  document.querySelectorAll('.mobile-del').forEach(b=>b.onclick=async()=>{await request(`/api/mobile-profiles/${b.dataset.id}`,{method:'DELETE'});await loadMobileProfiles();});
}
$('saveMobileBtn').onclick=async()=>{try{await post('/api/mobile-profiles',{name:$('mobileName').value,environment_id:Number($('mobileEnv').value)||null,platform:$('mobilePlatform').value,appium_url:$('mobileAppiumUrl').value,device_name:$('mobileDeviceName').value,username:$('mobileUser').value,password:$('mobilePassword').value,udid:$('mobileUdid').value,platform_version:$('mobileVersion').value,automation_name:$('mobileAutomation').value,app_package:$('mobilePackage').value,app_activity:$('mobileActivity').value,bundle_id:$('mobileBundleId').value,app_path:$('mobileAppPath').value,timeout_ms:30000});await loadMobileProfiles();setGlobal('Perfil Mobile guardado','good');}catch(e){setGlobal(e.message,'bad');}};

$('sqlConnection').onchange=async()=>{
  const id=Number($('sqlConnection').value||0);state.currentDb=id||null;state.currentSchema=null;$('schemaTree').innerHTML='';$('memoryList').innerHTML='';
  if(!id)return; try{await Promise.allSettled([loadSchema(false),loadMemory()]);}catch(e){showSqlMessage(e.message,true);}
};
function requireDb(){if(!state.currentDb)throw new Error('Selecciona una conexión PostgreSQL guardada.');return state.currentDb;}
function showSqlMessage(msg,error=false){$('sqlMessage').textContent=msg;$('sqlMessage').className=`message ${error?'error':''}`;}
function clearSqlResults(msg=''){$('sqlResults').replaceChildren();$('queryTiming').textContent='sin ejecutar';showSqlMessage(msg);}
function renderSqlRows(d){
  $('queryTiming').textContent=`${d.elapsed_ms??'?'} ms · ${d.row_count??0} filas${d.limit_applied?' · LIMIT preview':''}`;
  const table=$('sqlResults');table.replaceChildren();const thead=document.createElement('thead'),tr=document.createElement('tr');(d.columns||[]).forEach(c=>{const th=document.createElement('th');th.textContent=c;tr.appendChild(th)});thead.appendChild(tr);table.appendChild(thead);const tb=document.createElement('tbody');(d.rows||[]).forEach(row=>{const r=document.createElement('tr');row.forEach(v=>{const td=document.createElement('td');td.textContent=v===null?'NULL':String(v);r.appendChild(td)});tb.appendChild(r)});table.appendChild(tb);showSqlMessage(d.truncated?'Resultados truncados por límite de vista previa.':'Consulta completada.');
}
function renderSchema(d,filter=''){state.currentSchema=d;const q=filter.toLowerCase().trim();const items=(d.tables||[]).filter(t=>!q||t.name.toLowerCase().includes(q)||(t.columns||[]).some(c=>c.name.toLowerCase().includes(q)));$('schemaStats').textContent=`${items.length}/${(d.tables||[]).length} tablas · ${d.persisted_snapshot?'snapshot':d.cached?'caché':'DB'}`;const box=$('schemaTree');box.replaceChildren();items.forEach(t=>{const det=document.createElement('details'),sum=document.createElement('summary');sum.textContent=`${t.name} (${t.columns.length})`;det.appendChild(sum);det.addEventListener('toggle',()=>{if(!det.open||det.dataset.loaded)return;const ul=document.createElement('ul');t.columns.forEach(c=>{const li=document.createElement('li');li.textContent=`${c.name} — ${c.type}${t.primary_key?.includes(c.name)?' 🔑':''}`;ul.appendChild(li)});det.appendChild(ul);det.dataset.loaded='1'});box.appendChild(det)});}
async function loadSchema(force){const connection_id=requireDb();const d=await post('/api/sql/schema',{connection_id,force_refresh:!!force});renderSchema(d,$('schemaSearch').value);return d;}
$('sqlLoadSchema').onclick=()=>loadSchema(false).catch(e=>showSqlMessage(e.message,true));$('sqlRefreshSchema').onclick=()=>loadSchema(true).catch(e=>showSqlMessage(e.message,true));$('schemaSearch').oninput=()=>state.currentSchema&&renderSchema(state.currentSchema,$('schemaSearch').value);
async function loadMemory(){const d=await post('/api/sql/memory/list',{connection_id:requireDb(),limit:50});const box=$('memoryList');box.innerHTML='';(d.knowledge||[]).forEach(k=>{const x=document.createElement('div');x.className='memory-item';x.textContent=`✓ ${k.content}`;box.appendChild(x)});(d.queries||[]).slice(0,10).forEach(q=>{const x=document.createElement('div');x.className='memory-item';x.innerHTML=`<strong>${escapeHtml(q.question||'SQL manual')}</strong><code>${escapeHtml(q.sql)}</code>`;box.appendChild(x)});return d;}
$('saveKnowledgeBtn').onclick=async()=>{try{await post('/api/sql/memory/knowledge',{connection_id:requireDb(),content:$('knowledge').value,kind:'business_rule'});$('knowledge').value='';await loadMemory();}catch(e){showSqlMessage(e.message,true);}};
$('runSqlBtn').onclick=async()=>{clearSqlResults('Ejecutando…');try{const d=await post('/api/sql/query',{connection_id:requireDb(),sql:$('sqlEditor').value,max_rows:Number($('maxRows').value||200)});state.lastMemoryId=d.memory_id;$('verifyBtn').disabled=false;renderSqlRows(d);}catch(e){showSqlMessage(e.message,true);}};
$('clearSqlResults').onclick=()=>clearSqlResults('Resultados limpiados.');
$('aiStatusBtn').onclick=async()=>{
  $('aiRouterStatus').textContent='Leyendo AI Router…';
  try{
    const d=await request('/api/ai/status');
    const configured=(d.providers||[]).filter(p=>p.configured);
    $('aiRouterStatus').textContent=`${configured.length} proveedores configurados · orden: ${(d.order||[]).join(' → ')}`;
    showSqlMessage(configured.length?'AI Router listo para failover.':'No hay API keys configuradas.',configured.length===0);
  }catch(e){$('aiRouterStatus').textContent='Error leyendo AI Router';showSqlMessage(e.message,true);}
};
$('aiProbeBtn').onclick=async()=>{
  $('aiRouterStatus').textContent='Probando proveedores…';
  try{
    const d=await post('/api/ai/probe',{});
    const msg=(d.providers||[]).map(p=>`${p.provider}: ${p.ok?'OK '+(p.latency_ms??'')+' ms':'FAIL'}`).join(' · ');
    $('aiRouterStatus').textContent=msg||'Sin proveedores';
    showSqlMessage(msg,!(d.providers||[]).some(p=>p.ok));
  }catch(e){$('aiRouterStatus').textContent='Error probando proveedores';showSqlMessage(e.message,true);}
};
$('genBtn').onclick=async()=>{try{const d=await post('/api/sql/generate',{connection_id:requireDb(),question:$('question').value});$('sqlEditor').value=d.sql;state.lastMemoryId=d.memory_id;$('verifyBtn').disabled=false;$('memoryUsage').textContent=`${d.provider}/${d.model} · tablas al modelo: ${d.schema_table_count} · memoria: ${d.memory_examples_used}+${d.knowledge_used}`;}catch(e){showSqlMessage(e.message,true);}};
$('askBtn').onclick=async()=>{clearSqlResults('Generando SQL con AI Router…');$('aiStatus').textContent='AI Router generando SQL…';try{const d=await post('/api/sql/ask',{connection_id:requireDb(),question:$('question').value,max_rows:Number($('maxRows').value||200)});$('sqlEditor').value=d.sql;state.lastMemoryId=d.memory_id;$('verifyBtn').disabled=false;$('memoryUsage').textContent=`${d.provider}/${d.model} · memoria: ${d.memory_examples_used}+${d.knowledge_used}`;renderSqlRows(d);$('aiStatus').textContent=`IA · ${d.provider}/${d.model}`;}catch(e){$('aiStatus').textContent='AI Router con error';showSqlMessage(e.message,true);}};
$('verifyBtn').onclick=async()=>{try{await post('/api/sql/memory/verify',{connection_id:requireDb(),memory_id:state.lastMemoryId,verified:true});showSqlMessage('SQL confirmado y guardado como experiencia válida.');await loadMemory();}catch(e){showSqlMessage(e.message,true);}};

$('sendApiBtn').onclick=async()=>{const started=performance.now();$('apiResponse').textContent='Enviando…';try{const d=await post('/api/api-lab/request',{profile_id:Number($('apiLabProfile').value),method:$('apiMethod').value,path:$('apiPath').value,headers:parseJson($('apiHeaders').value,{}),params:{},body:$('apiBody').value.trim()?parseJson($('apiBody').value):null});$('apiTiming').textContent=`HTTP ${d.status_code} · ${d.elapsed_ms} ms`;$('apiResponse').textContent=JSON.stringify(d,null,2);}catch(e){$('apiTiming').textContent=`${Math.round(performance.now()-started)} ms`;$('apiResponse').textContent=e.message;}};

function updateBrowser(d){if(d.session_id)state.browserSession=d.session_id;$('browserState').textContent=state.browserSession?'activo':'cerrado';$('browserState').className=`badge ${state.browserSession?'good':'muted'}`;$('browserMeta').textContent=`${d.title||''} · ${d.url||''}`;if(d.data_url)$('browserShot').src=d.data_url;$('browserLogs').textContent=JSON.stringify({console:d.console||[],failed_requests:d.failed_requests||[],http_errors:d.http_errors||[]},null,2);}
$('startBrowserBtn').onclick=async()=>{try{const d=await post('/api/browser/start',{profile_id:Number($('workspaceWebProfile').value)});updateBrowser(d);}catch(e){setGlobal(e.message,'bad');}};
$('stopBrowserBtn').onclick=async()=>{if(!state.browserSession)return;await request(`/api/browser/${state.browserSession}`,{method:'DELETE'});state.browserSession=null;updateBrowser({});$('browserShot').removeAttribute('src');};
$('gotoBtn').onclick=async()=>{if(!state.browserSession)return setGlobal('Inicia el navegador primero','bad');try{updateBrowser(await post('/api/browser/action',{session_id:state.browserSession,action:'goto',config:{url:$('gotoUrl').value,screenshot:true}}));}catch(e){setGlobal(e.message,'bad');}};
document.querySelectorAll('[data-web-action]').forEach(btn=>btn.onclick=async()=>{if(!state.browserSession)return setGlobal('Inicia el navegador primero','bad');const action=btn.dataset.webAction;const cfg={screenshot:true};if(action!=='screenshot'){cfg.selector=$('webSelector').value;}if(action==='fill')cfg.value=$('webValue').value;if(action==='expect_text')cfg.expected=$('webValue').value;try{updateBrowser(await post('/api/browser/action',{session_id:state.browserSession,action,config:cfg}));setGlobal(`${action} OK`,'good');}catch(e){setGlobal(e.message,'bad');}});
$('browserShot').onclick=async(e)=>{if(!state.browserSession||!$('browserShot').naturalWidth)return;const r=$('browserShot').getBoundingClientRect();const x=(e.clientX-r.left)*($('browserShot').naturalWidth/r.width),y=(e.clientY-r.top)*($('browserShot').naturalHeight/r.height);try{updateBrowser(await post('/api/browser/action',{session_id:state.browserSession,action:'click_xy',config:{x,y,screenshot:true}}));}catch(err){setGlobal(err.message,'bad');}};


function updateMobile(d){if(d.session_id)state.mobileSession=d.session_id;$('mobileState').textContent=state.mobileSession?'activo':'cerrado';$('mobileState').className=`badge ${state.mobileSession?'good':'muted'}`;$('mobileMeta').textContent=`${d.platform||''} · ${d.device_name||''} · ${d.appium_url||''}`;if(d.data_url)$('mobileShot').src=d.data_url;$('mobileLogs').textContent=JSON.stringify({remote_session_id:d.remote_session_id||'',action:d.action||'',elapsed_ms:d.action_elapsed_ms||''},null,2);}
$('startMobileBtn').onclick=async()=>{const id=Number($('workspaceMobileProfile').value||0);if(!id)return setGlobal('Selecciona un perfil Mobile','bad');try{updateMobile(await post('/api/mobile/start',{profile_id:id}));setGlobal('Sesión Appium iniciada','good');}catch(e){setGlobal(e.message,'bad');}};
$('stopMobileBtn').onclick=async()=>{if(!state.mobileSession)return;try{await request(`/api/mobile/${state.mobileSession}`,{method:'DELETE'});}finally{state.mobileSession=null;updateMobile({});$('mobileShot').removeAttribute('src');}};
document.querySelectorAll('[data-mobile-action]').forEach(btn=>btn.onclick=async()=>{if(!state.mobileSession)return setGlobal('Inicia Appium primero','bad');const action=btn.dataset.mobileAction,cfg={screenshot:true};if(!['back','screenshot'].includes(action)){cfg[$('mobileLocatorType').value]=$('mobileLocator').value;}if(action==='fill')cfg.value=$('mobileValue').value;try{updateMobile(await post('/api/mobile/action',{session_id:state.mobileSession,action,config:cfg}));setGlobal(`Mobile ${action} OK`,'good');}catch(e){setGlobal(e.message,'bad');}});


async function showRouterStatusWorkspace(){
  try{const d=await request('/api/ai/status');const ok=(d.providers||[]).filter(p=>p.configured);$('caseImportStatus').textContent=`AI Router: ${ok.length} configurados · ${(d.order||[]).join(' → ')}`;$('caseImportStatus').className='message';}catch(e){$('caseImportStatus').textContent=e.message;$('caseImportStatus').className='message error';}
}
$('refreshAiRouterBtn').onclick=showRouterStatusWorkspace;
$('interpretCasesBtn').onclick=async()=>{
  const file=$('caseImportFile').files?.[0];
  if(!file){$('caseImportStatus').textContent='Selecciona un archivo .xlsx, .json o .csv.';$('caseImportStatus').className='message error';return;}
  const fd=new FormData();fd.append('file',file);
  if($('importEnv').value)fd.append('environment_id',$('importEnv').value);
  if($('importWeb').value)fd.append('web_profile_id',$('importWeb').value);
  if($('importDb').value)fd.append('db_connection_id',$('importDb').value);
  if($('importApi').value)fd.append('api_profile_id',$('importApi').value);
  if($('importMobile').value)fd.append('mobile_profile_id',$('importMobile').value);
  fd.append('auto_save',$('importAutoSave').checked?'true':'false');
  $('caseImportStatus').textContent='Leyendo archivo, consultando memoria y planificando casos…';$('caseImportStatus').className='message';
  $('interpretCasesBtn').disabled=true;
  try{
    const d=await requestForm('/api/case-import/interpret',fd);
    const savedByKey=Object.fromEntries((d.saved||[]).map(x=>[x.case_key,x.id]));state.lastImportedSavedIds=(d.saved||[]).map(x=>Number(x.id));$('runImportedSuiteBtn').disabled=state.lastImportedSavedIds.length===0;
    $('caseImportStatus').textContent=`${d.cases?.length||0} casos interpretados · ${d.saved?.length||0} guardados · fuente ${d.source?.source_type||''}`;
    $('caseImportPreview').innerHTML=(d.cases||[]).map(c=>`<div class="list-item import-case"><div><strong>${escapeHtml(c.case_key)} · ${escapeHtml(c.name)}</strong><br><small>${escapeHtml(c.description||'')}</small><br><span class="${c.review_required?'status-blocked':'status-pass'}">${c.review_required?'REVISAR: '+escapeHtml(c.review_reason||''):'PLAN LISTO'}</span><details><summary>Ver plan</summary><pre class="output compact">${escapeHtml(JSON.stringify(c.plan,null,2))}</pre></details></div><div>${savedByKey[c.case_key]?`<button class="danger imported-run" data-id="${savedByKey[c.case_key]}">Ejecutar</button>`:''}</div></div>`).join('')||'<div class="hint">No se detectaron casos.</div>';
    document.querySelectorAll('.imported-run').forEach(b=>b.onclick=()=>runCase(Number(b.dataset.id)));
    await Promise.all([loadCases(),loadDashboard()]);
  }catch(e){$('caseImportStatus').textContent=e.message;$('caseImportStatus').className='message error';}
  finally{$('interpretCasesBtn').disabled=false;}
};


$('runImportedSuiteBtn').onclick=async()=>{
  const ids=[...state.lastImportedSavedIds];if(!ids.length)return;
  $('runImportedSuiteBtn').disabled=true;const summary=[];
  for(let i=0;i<ids.length;i++){
    $('caseImportStatus').textContent=`Ejecutando suite importada ${i+1}/${ids.length}…`;
    try{const d=await post(`/api/test-cases/${ids[i]}/run`,{});summary.push({id:ids[i],status:d.status,duration_ms:d.duration_ms});}
    catch(e){summary.push({id:ids[i],status:'ERROR',error:e.message});}
  }
  const pass=summary.filter(x=>x.status==='PASS').length,fail=summary.filter(x=>x.status==='FAIL').length,blocked=summary.filter(x=>x.status==='BLOCKED').length;
  $('caseImportStatus').textContent=`Suite terminada: ${pass} PASS · ${fail} FAIL · ${blocked} BLOCKED · ${summary.length-pass-fail-blocked} ERROR`;
  $('caseImportPreview').insertAdjacentHTML('afterbegin',`<details open><summary>Resultado de suite</summary><pre class="output compact">${escapeHtml(JSON.stringify(summary,null,2))}</pre></details>`);
  $('runImportedSuiteBtn').disabled=false;await Promise.all([loadDashboard(),loadExecutions()]);
};

function caseTemplate(){const db=state.dbConnections[0]?.id||1,web=state.webProfiles[0]?.id||1,api=state.apiProfiles[0]?.id||1;return {variables:{},steps:[{type:'db.query',name:'Precondición DB',connection_id:db,sql:'SELECT CURRENT_TIMESTAMP AS now',expect_min_rows:1},{type:'web.start',name:'Abrir Web QA',profile_id:web},{type:'web.screenshot',name:'Evidencia inicial',evidence:true},{type:'api.request',name:'Health API',profile_id:api,method:'GET',path:'/',expected_status:200},{type:'web.stop',name:'Cerrar navegador'}]};}
function clearCase(){state.currentCaseId=null;$('caseKey').value='';$('caseName').value='';$('caseDescription').value='';$('casePlan').value=JSON.stringify(caseTemplate(),null,2);$('caseRunResult').textContent='';}
async function loadCases(){const d=await request('/api/test-cases');const items=d.items||[];$('caseList').innerHTML=items.map(c=>`<div class="list-item"><div><strong>${escapeHtml(c.case_key)} · ${escapeHtml(c.name)}</strong><br><small>${escapeHtml(c.description||'')}</small></div><div class="row"><button class="secondary case-edit" data-id="${c.id}">Editar</button><button class="danger case-run" data-id="${c.id}">Run</button></div></div>`).join('')||'<div class="hint">Sin casos guardados.</div>';document.querySelectorAll('.case-edit').forEach(b=>b.onclick=()=>editCase(Number(b.dataset.id)));document.querySelectorAll('.case-run').forEach(b=>b.onclick=()=>runCase(Number(b.dataset.id)));if(!$('casePlan').value)clearCase();}
async function editCase(id){const c=await request(`/api/test-cases/${id}`);state.currentCaseId=id;$('caseKey').value=c.case_key;$('caseName').value=c.name;$('caseDescription').value=c.description||'';$('caseEnv').value=c.environment_id||'';$('casePlan').value=JSON.stringify(c.plan,null,2);}
$('newCaseBtn').onclick=clearCase;$('saveCaseBtn').onclick=async()=>{try{const d=await post('/api/test-cases',{case_key:$('caseKey').value,name:$('caseName').value,description:$('caseDescription').value,environment_id:Number($('caseEnv').value)||null,plan:parseJson($('casePlan').value),is_active:true});state.currentCaseId=d.id;await loadCases();setGlobal('Caso guardado','good');}catch(e){setGlobal(e.message,'bad');}};
async function runCase(id){$('caseRunResult').textContent='Ejecutando caso…';try{const d=await post(`/api/test-cases/${id}/run`,{});$('caseRunResult').innerHTML=`<strong class="status-${d.status.toLowerCase()}">${d.status}</strong> · ${d.duration_ms} ms · ${d.steps.length} pasos`;await Promise.all([loadCases(),loadDashboard(),loadExecutions()]);}catch(e){$('caseRunResult').textContent=e.message;}}
$('runCaseBtn').onclick=async()=>{if(!state.currentCaseId){$('saveCaseBtn').click();setGlobal('Guarda el caso antes de ejecutarlo','bad');return;}await runCase(state.currentCaseId);};

function renderExecutions(items,container){if(!items.length){container.innerHTML='<div class="hint">Sin ejecuciones.</div>';return;}container.innerHTML=`<div class="table-wrap"><table><thead><tr><th>ID</th><th>Caso</th><th>Estado</th><th>Inicio</th><th>Tiempo</th><th>Reportes</th></tr></thead><tbody>${items.map(x=>`<tr><td>${x.id}</td><td>${escapeHtml(x.case_key||'')} · ${escapeHtml(x.case_name||'')}</td><td class="status-${String(x.status).toLowerCase()}">${escapeHtml(x.status)}</td><td>${escapeHtml(x.started_at||'')}</td><td>${x.duration_ms??''} ms</td><td><a href="/api/executions/${x.id}/report/html" target="_blank">HTML</a> · <a href="/api/executions/${x.id}/report/json" target="_blank">JSON</a></td></tr>`).join('')}</tbody></table></div>`;}
async function loadExecutions(){const d=await request('/api/executions');renderExecutions(d.items||[],$('executionList'));}
$('refreshExecutions').onclick=loadExecutions;

$('saveAgentKnowledgeBtn').onclick=async()=>{try{const content=$('agentKnowledgeContent').value.trim();if(!content)throw new Error('Escribe el conocimiento que quieres guardar.');await post('/api/agent-memory/knowledge',{kind:'qa_rule',title:$('agentKnowledgeTitle').value,content,scope:'global',verified:true});$('agentKnowledgeTitle').value='';$('agentKnowledgeContent').value='';$('agentKnowledgeMsg').textContent='Conocimiento guardado en memoria propia.';$('agentKnowledgeMsg').className='message';await renderLearning();}catch(e){$('agentKnowledgeMsg').textContent=e.message;$('agentKnowledgeMsg').className='message error';}};

async function renderLearning(){
  let extra='';
  try{
    const [exp,mem,router]=await Promise.all([request('/api/learning/experiences?limit=20'),request('/api/agent-memory?limit=20'),request('/api/ai/status')]);
    const items=exp.items||[];
    const providers=(router.providers||[]).map(p=>`<div class="list-item"><strong>${escapeHtml(p.name)} · ${escapeHtml(p.model||'')}</strong><span class="${p.configured?'status-pass':'status-blocked'}">${p.configured?(p.available_now?'LISTO':'COOLDOWN'):'SIN KEY'}</span></div>`).join('');
    const experiences=items.length?items.map(x=>`<div class="list-item"><div><strong>${escapeHtml(x.case_key||'')}</strong><br><small>${escapeHtml(x.summary?.case_name||'')} · ${x.summary?.step_count||0} pasos</small></div><span class="status-${String(x.status).toLowerCase()}">${escapeHtml(x.status)}</span></div>`).join(''):'<div class="hint">Aún no hay ejecuciones aprendidas.</div>';
    extra=`<h3>AI Router</h3>${providers}<h3>Memoria propia</h3><p>${mem.knowledge?.length||0} reglas · ${mem.playbooks?.length||0} playbooks · ${mem.agent_runs?.length||0} llamadas recientes.</p><h3>Experiencias QA recientes</h3>${experiences}`;
  }catch(e){extra=`<div class="message error">${escapeHtml(e.message)}</div>`;}
  $('learningInfo').innerHTML=`La memoria persistente vive en <code>data/memory.sqlite3</code> y <code>data/agent_memory.sqlite3</code>. No depende de OpenRouter, Groq, Gemini, Mistral ni de un modelo concreto. Las API keys permanecen fuera de la UI y los proveedores se usan con failover. ${extra}`;
}
async function runSystemCheck(){try{const d=await request('/api/system/check');$('systemCheck').innerHTML=Object.entries(d.commands||{}).map(([k,v])=>`<div class="list-item"><strong>${escapeHtml(k)}</strong><span class="${v.ok?'status-pass':'status-fail'}">${v.ok?'OK':'NO DETECTADO'} ${escapeHtml(v.path||'')}</span></div>`).join('')+`<div class="list-item"><strong>Playwright Python</strong><span class="${d.playwright_python?'status-pass':'status-fail'}">${d.playwright_python?'OK':'NO'}</span></div>`;}catch(e){$('systemCheck').textContent=e.message;}}
$('runSystemCheck').onclick=runSystemCheck;

bootstrap().catch(e=>{console.error(e);showAuth(false);$('authMessage').textContent=e.message;});
