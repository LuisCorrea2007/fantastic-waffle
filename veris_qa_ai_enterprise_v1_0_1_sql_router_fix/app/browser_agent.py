from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from .ai_router import complete_json
from .browser_manager import browser_manager
from .agent_memory import retrieve_context
from .security_utils import redact_obj, redact_text

ALLOWED_ACTIONS={'click','fill','press','select','check','uncheck','hover','goto','wait','finish','fail'}
ALLOWED_ASSERTIONS={'visible','hidden','text_contains','page_text_contains','url_contains'}


def _locator_cfg(locator: dict | None) -> dict:
    locator=locator or {}; out={}
    for k in ('selector','text','label','placeholder','role','name','exact'):
        if k in locator and locator[k] not in (None,''): out[k]=locator[k]
    return out


def _validate_action(obj: Any) -> dict:
    if not isinstance(obj,dict): raise ValueError('La acción del agente debe ser un objeto JSON.')
    action=str(obj.get('action') or '').lower().strip()
    if action not in ALLOWED_ACTIONS: raise ValueError(f'Acción del agente no permitida: {action}')
    obj=dict(obj); obj['action']=action
    if action in {'click','fill','press','select','check','uncheck','hover'} and not _locator_cfg(obj.get('locator')):
        raise ValueError(f'{action} requiere locator.')
    if action=='finish':
        assertion=obj.get('assertion')
        if not isinstance(assertion,dict) or str(assertion.get('type') or '') not in ALLOWED_ASSERTIONS:
            raise ValueError('finish requiere una assertion determinista.')
    return obj


def _system_prompt() -> str:
    return '''You are the browser operator inside an enterprise QA runner.
You do NOT decide the overall PASS/FAIL. You choose one safe browser action at a time.
Return ONLY one JSON object.
Allowed actions: click, fill, press, select, check, uncheck, hover, goto, wait, finish, fail.
For element actions use locator with one of: selector, text, label, placeholder, or role+name.
Never invent secrets. If a fill needs a credential, use value_from with one of the AVAILABLE VARIABLES.
Never submit destructive business actions unless the QA goal explicitly requires it.
Stay on the configured QA application unless the goal explicitly names another authorized URL.
Use finish only when you can provide a deterministic assertion that the backend can execute.
Allowed finish assertion types: visible, hidden, text_contains, page_text_contains, url_contains.
Examples:
{"action":"click","locator":{"text":"Aceptar"},"reason":"..."}
{"action":"fill","locator":{"placeholder":"Usuario"},"value_from":"web.username","reason":"..."}
{"action":"finish","assertion":{"type":"visible","locator":{"text":"Consentimiento"}},"reason":"goal verified"}
{"action":"finish","assertion":{"type":"page_text_contains","expected":"Cita agendada"},"reason":"expected text present"}
If blocked by CAPTCHA, OTP, missing permission, or an element you cannot identify, return action=fail with a concise reason.'''


async def _ask_action(user_prompt: str) -> tuple[dict,dict]:
    obj,meta=await asyncio.to_thread(complete_json,_system_prompt(),user_prompt,'browser_agent',700,0.0)
    return _validate_action(obj),meta


async def run_browser_goal(session_id: str, goal: str, expected: str = '', variables: dict | None = None, max_actions: int = 12) -> dict:
    variables=variables or {}; max_actions=max(1,min(int(max_actions),25))
    trace=[]; provider_trace=[]
    memory=retrieve_context(goal+' '+expected,limit=6)
    memory_brief={
        'knowledge':[{'title':x.get('title'),'content':x.get('content')} for x in memory.get('knowledge',[])[:4]],
        'playbooks':[{'case_key':x.get('case_key'),'title':x.get('title'),'lessons':x.get('lessons')} for x in memory.get('playbooks',[])[:3]],
    }
    available_vars=[k for k in variables.keys() if k in {'web.username','web.password'} or k.startswith('test.') or k.startswith('patient.')]

    for turn in range(1,max_actions+1):
        snapshot=await browser_manager.page_snapshot(session_id)
        # Structural snapshot can still include business text; redact obvious PII before external APIs.
        safe_snapshot=redact_obj(snapshot)
        prompt=f'''QA GOAL:\n{goal}\n\nEXPECTED RESULT:\n{expected or '(not specified)'}\n\nTURN: {turn}/{max_actions}\nAVAILABLE VARIABLES (names only): {available_vars}\n\nCURRENT PAGE SNAPSHOT:\n{json.dumps(safe_snapshot,ensure_ascii=False)}\n\nRELEVANT PERSISTENT QA MEMORY:\n{json.dumps(memory_brief,ensure_ascii=False)}\n\nPREVIOUS ACTION TRACE:\n{json.dumps(trace[-8:],ensure_ascii=False)}\n\nChoose the next single action.'''
        action,meta=await _ask_action(prompt); provider_trace.append(meta)
        kind=action['action']; item={'turn':turn,'decision':redact_obj(action),'provider':meta.get('provider'),'model':meta.get('model')}
        try:
            if kind=='fail':
                item['status']='BLOCKED'; trace.append(item)
                return {'status':'BLOCKED','reason':str(action.get('reason') or 'Agente bloqueado.'),'trace':trace,'providers':provider_trace}
            if kind=='finish':
                assertion=action['assertion']; atype=assertion['type']; cfg=_locator_cfg(assertion.get('locator'))
                if atype=='visible':
                    await browser_manager.action(session_id,'expect_visible',**cfg,screenshot=False)
                elif atype=='hidden':
                    await browser_manager.action(session_id,'expect_hidden',**cfg,screenshot=False)
                elif atype=='text_contains':
                    cfg['expected']=str(assertion.get('expected') or '')
                    await browser_manager.action(session_id,'expect_text',**cfg,screenshot=False)
                elif atype=='page_text_contains':
                    await browser_manager.action(session_id,'expect_page_text',expected=str(assertion.get('expected') or ''),screenshot=False)
                elif atype=='url_contains':
                    await browser_manager.action(session_id,'expect_url_contains',expected=str(assertion.get('expected') or ''),screenshot=False)
                item['status']='PASS'; trace.append(item)
                return {'status':'PASS','assertion':assertion,'reason':str(action.get('reason') or ''),'trace':trace,'providers':provider_trace}

            cfg=_locator_cfg(action.get('locator'))
            if kind=='fill':
                if action.get('value_from'):
                    key=str(action['value_from'])
                    if key not in variables: raise ValueError(f'Variable solicitada por el agente no disponible: {key}')
                    cfg['value']=variables[key]
                else:
                    cfg['value']=str(action.get('value') or '')
            elif kind=='press': cfg['key']=str(action.get('key') or 'Enter')
            elif kind=='select': cfg['value']=str(action.get('value') or '')
            elif kind=='goto': cfg={'url':str(action.get('url') or '')}
            elif kind=='wait': cfg={'ms':max(100,min(int(action.get('ms') or 700),10000))}
            await browser_manager.action(session_id,kind,**cfg,screenshot=False)
            item['status']='PASS'; trace.append(item)
        except Exception as exc:
            item['status']='ERROR'; item['error']=redact_text(str(exc),1000); trace.append(item)
            # Let the agent see one failed locator/action and recover on the next turn.
            continue
    return {'status':'BLOCKED','reason':f'El agente alcanzó el máximo de {max_actions} acciones sin una assertion final verificable.','trace':trace,'providers':provider_trace}
