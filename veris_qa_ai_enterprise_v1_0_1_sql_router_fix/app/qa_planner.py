from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from .ai_router import complete_json
from .agent_memory import retrieve_context
from .security_utils import redact_obj

ALLOWED_STEPS = {
    'db.query','db.ai_query','api.request','web.start','web.goto','web.click','web.fill','web.press','web.select',
    'web.check','web.uncheck','web.hover','web.expect_visible','web.expect_hidden','web.expect_text','web.expect_page_text',
    'web.expect_url_contains','web.screenshot','web.agent','web.stop','mobile.start','mobile.click','mobile.fill','mobile.back','mobile.expect_visible','mobile.screenshot','mobile.agent','mobile.stop','wait','assert.equals'
}


def _system_prompt() -> str:
    return '''You are the QA planning agent for Veris QA AI. Convert use cases into deterministic executable test plans.
Return ONLY valid JSON with this shape: {"cases":[...]}. Never return markdown.
The AI plans; the runner executes and determines PASS/FAIL from real evidence.

SUPPORTED STEP TYPES:
- web.start: {type,name,profile_id}
- web.goto: {type,name,url}
- web.click/fill/press/select/check/uncheck/hover: direct locator action. Locator fields supported by runner: selector, text, label, placeholder, role + name.
- web.expect_visible / web.expect_hidden / web.expect_text / web.expect_page_text / web.expect_url_contains: deterministic assertions.
- web.agent: {type,name,goal,expected,max_actions,evidence}. Use this when natural-language steps do not provide reliable selectors. The runtime agent interacts with the actual DOM and MUST finish with a deterministic assertion.
- web.screenshot / web.stop.
- mobile.start: {type,name,profile_id}
- mobile.click/fill/back/expect_visible/screenshot/stop: Appium actions. Locators: accessibility_id, id, xpath, text, android_uiautomator, ios_predicate.
- mobile.agent: {type,name,goal,expected,max_actions,evidence}. Use when Android/iOS steps are described in business language without locators.
- db.query: use only if exact SQL is present in the source. READ ONLY only.
- db.ai_query: {type,name,connection_id,question,expect?}. Use when DB validation is required but the exact query is not given. The SQL specialist will use the real schema at runtime.
- api.request: only when the source or memory provides a real endpoint/path. Never invent API endpoints.
- wait: {type,name,ms}
- assert.equals: {type,name,left,right}

DB EXPECT FORMAT (optional):
{"min_rows":1,"max_rows":10,"first_row":{"estado":{"equals":"A"},"fecha":{"not_null":true},"consentimiento":{"is_null":true}}}
Supported operators for first_row fields: equals, not_equals, is_null, not_null, contains, in.

RULES:
1. Use only the supplied profile IDs. Never invent IDs.
2. If the case explicitly refers to Android, iOS, mobile or an app and MOBILE_PROFILE_ID is available, prefer Mobile/Appium steps.
3. If the case is Web UI-focused and a WEB_PROFILE_ID is available, start the browser, execute the case, capture evidence, then stop it.
4. If the precondition or expected result explicitly requires database validation and DB_CONNECTION_ID is available, include db.ai_query or db.query.
5. Use API only when the case explicitly concerns an API or a known endpoint is present.
6. Never generate destructive SQL.
7. Do not invent credentials; agents can use web.username/web.password or mobile.username/mobile.password variable names without exposing the secret to the LLM.
8. Every runnable case must contain at least one deterministic assertion or a web.agent step that is required to finish with a deterministic assertion.
9. If required configuration is missing, set review_required=true and explain review_reason. Still create the safest draft plan possible.
10. Preserve the user's case key, title, precondition, action and expected result.
11. Prefer web.agent/mobile.agent for business-language actions such as “inicia sesión”, “acepta el formulario”, “cierra sesión”, unless exact selectors are present in source/memory.'''


def _case_text(case: dict) -> str:
    return '\n'.join(str(case.get(k) or '') for k in ('case_key','name','category','description','precondition','action','expected'))


def _validate_case(raw: dict, defaults: dict) -> dict:
    if not isinstance(raw,dict): raise ValueError('Caso planificado inválido.')
    plan=raw.get('plan') or {}; steps=plan.get('steps') or []
    if not isinstance(steps,list): raise ValueError('plan.steps debe ser lista.')
    clean=[]; assertions=0
    for step in steps:
        if not isinstance(step,dict): continue
        kind=str(step.get('type') or '').strip()
        if kind not in ALLOWED_STEPS: continue
        s=dict(step)
        # Force configured IDs to prevent hallucinated profile references.
        if kind.startswith('web.') and kind=='web.start':
            if defaults.get('web_profile_id'): s['profile_id']=int(defaults['web_profile_id'])
            else: continue
        if kind.startswith('mobile.') and kind=='mobile.start':
            if defaults.get('mobile_profile_id'): s['profile_id']=int(defaults['mobile_profile_id'])
            else: continue
        if kind in {'db.query','db.ai_query'}:
            if defaults.get('db_connection_id'): s['connection_id']=int(defaults['db_connection_id'])
            else: continue
        if kind=='api.request':
            if defaults.get('api_profile_id'): s['profile_id']=int(defaults['api_profile_id'])
            else: continue
        if kind.startswith('web.expect_') or kind=='mobile.expect_visible' or kind=='assert.equals' or kind=='api.request' or (kind.startswith('db.') and s.get('expect')):
            assertions+=1
        if kind in {'web.agent','mobile.agent'}: assertions+=1
        clean.append(s)
    if clean and any(x.get('type')=='web.start' for x in clean) and not any(x.get('type')=='web.stop' for x in clean):
        clean.append({'type':'web.stop','name':'Cerrar navegador QA'})
    if clean and any(x.get('type')=='mobile.start' for x in clean) and not any(x.get('type')=='mobile.stop' for x in clean):
        clean.append({'type':'mobile.stop','name':'Cerrar sesión Appium'})
    return {
        'case_key':str(raw.get('case_key') or '').strip()[:100],
        'name':str(raw.get('name') or '').strip()[:300],
        'description':str(raw.get('description') or '')[:4000],
        'review_required':bool(raw.get('review_required')) or assertions==0,
        'review_reason':str(raw.get('review_reason') or ('El plan no contiene assertion determinista.' if assertions==0 else ''))[:1000],
        'plan':{'variables':plan.get('variables') or {},'steps':clean,'source':plan.get('source') or {}},
    }


def _heuristic_plan(case: dict, defaults: dict) -> dict:
    steps=[]
    text=_case_text(case).lower()
    db_words=('base de datos','database','db','persist','registro','consent','estado','tabla','query','sql')
    if defaults.get('db_connection_id') and any(w in text for w in db_words) and case.get('precondition'):
        steps.append({'type':'db.ai_query','name':'Validar precondición en PostgreSQL','connection_id':defaults['db_connection_id'],'question':case.get('precondition'),'expect':{'min_rows':1}})
    mobile_words=('android','ios','mobile','movil','móvil','app ','aplicacion','aplicación')
    use_mobile=bool(defaults.get('mobile_profile_id') and any(w in text for w in mobile_words))
    if use_mobile:
        steps.append({'type':'mobile.start','name':'Abrir app QA','profile_id':defaults['mobile_profile_id']})
        steps.append({'type':'mobile.agent','name':'Ejecutar flujo Mobile','goal':case.get('action') or case.get('name'),'expected':case.get('expected') or 'Validar el resultado esperado','max_actions':12,'evidence':True})
        steps.append({'type':'mobile.screenshot','name':'Evidencia Mobile final','evidence':True})
        steps.append({'type':'mobile.stop','name':'Cerrar sesión Appium'})
    elif defaults.get('web_profile_id'):
        steps.append({'type':'web.start','name':'Abrir aplicación QA','profile_id':defaults['web_profile_id']})
        steps.append({'type':'web.agent','name':'Ejecutar flujo del caso','goal':case.get('action') or case.get('name'),'expected':case.get('expected') or 'Validar el resultado esperado del caso','max_actions':12,'evidence':True})
        steps.append({'type':'web.screenshot','name':'Evidencia final','evidence':True})
        steps.append({'type':'web.stop','name':'Cerrar navegador QA'})
    if defaults.get('db_connection_id') and any(w in text for w in db_words) and case.get('expected'):
        steps.insert(-1 if steps else len(steps),{'type':'db.ai_query','name':'Validar resultado en PostgreSQL','connection_id':defaults['db_connection_id'],'question':case.get('expected'),'expect':{'min_rows':1}})
    return {'case_key':case.get('case_key'),'name':case.get('name'),'description':case.get('description') or '', 'review_required':True,'review_reason':'Plan heurístico: revisar antes de producción.' if steps else 'Falta configurar Web/DB para este caso.','plan':{'variables':{},'steps':steps,'source':{'imported':True}}}


async def plan_cases(cases: list[dict], defaults: dict, source_meta: dict | None=None) -> tuple[list[dict],dict]:
    results=[]; routing=[]
    prebuilt=[]; pending=[]
    for case in cases:
        if case.get('prebuilt_plan'):
            raw={'case_key':case.get('case_key'),'name':case.get('name'),'description':case.get('description'),'plan':case['prebuilt_plan'],'review_required':False}
            prebuilt.append(_validate_case(raw,defaults))
        else: pending.append(case)
    results.extend(prebuilt)
    batch_size=max(1,min(int(os.getenv('AI_CASE_BATCH_SIZE','6')),10))
    for start in range(0,len(pending),batch_size):
        batch=pending[start:start+batch_size]
        query=' '.join(_case_text(c) for c in batch)
        memory=retrieve_context(query,limit=8)
        context={
            'WEB_PROFILE_ID':defaults.get('web_profile_id'),
            'DB_CONNECTION_ID':defaults.get('db_connection_id'),
            'API_PROFILE_ID':defaults.get('api_profile_id'),
            'MOBILE_PROFILE_ID':defaults.get('mobile_profile_id'),
            'ENVIRONMENT_ID':defaults.get('environment_id'),
            'source':source_meta or {},
            'memory':memory,
            'cases':[{k:c.get(k) for k in ('case_key','name','category','description','precondition','action','expected','raw')} for c in batch],
        }
        try:
            obj,meta=await asyncio.to_thread(complete_json,_system_prompt(),json.dumps(redact_obj(context),ensure_ascii=False),'case_planner',5000,0.0)
            routing.append(meta)
            raw_cases=(obj or {}).get('cases') if isinstance(obj,dict) else None
            if not isinstance(raw_cases,list): raise ValueError('La IA no devolvió cases como lista.')
            by_key={str(x.get('case_key')):x for x in raw_cases if isinstance(x,dict)}
            for c in batch:
                raw=by_key.get(str(c.get('case_key')))
                if not raw:
                    results.append(_heuristic_plan(c,defaults)); continue
                if not raw.get('case_key'): raw['case_key']=c.get('case_key')
                if not raw.get('name'): raw['name']=c.get('name')
                results.append(_validate_case(raw,defaults))
        except Exception as exc:
            routing.append({'provider':'fallback','model':'heuristic','error':str(exc)})
            results.extend(_heuristic_plan(c,defaults) for c in batch)
    return results,{'routing':routing,'planned':len(results),'prebuilt':len(prebuilt)}
