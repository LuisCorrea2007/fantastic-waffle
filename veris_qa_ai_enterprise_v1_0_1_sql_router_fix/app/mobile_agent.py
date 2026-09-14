from __future__ import annotations
import asyncio, json
from .ai_router import complete_json
from .agent_memory import retrieve_context
from .mobile_manager import mobile_manager
from .security_utils import redact_text

ALLOWED={'click','fill','back','wait','finish','fail'}


def _validate(o):
    if not isinstance(o,dict): raise ValueError('Acción Mobile IA inválida.')
    a=str(o.get('action') or '').lower().strip()
    if a not in ALLOWED: raise ValueError(f'Acción Mobile no permitida: {a}')
    o=dict(o);o['action']=a
    if a in {'click','fill'} and not isinstance(o.get('locator'),dict): raise ValueError(f'{a} requiere locator.')
    if a=='finish' and not isinstance(o.get('assertion'),dict): raise ValueError('finish requiere assertion.')
    return o


def _system():
    return '''You are the mobile QA operator for Appium. Return ONLY one JSON object for one action.
Allowed: click, fill, back, wait, finish, fail.
Locator can use accessibility_id, id, xpath, text, android_uiautomator, ios_predicate.
Never invent credentials. For fill use value_from=mobile.username/mobile.password/web.username/web.password/test.* when needed; the backend resolves it without exposing secrets.
finish MUST include deterministic assertion: {"type":"visible","locator":{...}}.
If blocked by OTP/CAPTCHA/missing device permission, use fail with reason.'''


async def run_mobile_goal(session_id:str,goal:str,expected:str='',variables:dict|None=None,max_actions:int=12)->dict:
    variables=variables or {};trace=[];providers=[];mem=retrieve_context(goal+' '+expected,6);available=[k for k in variables if k.startswith(('mobile.','web.','test.','patient.'))]
    for turn in range(1,max(1,min(int(max_actions),25))+1):
        src=await mobile_manager.source(session_id,30000)
        prompt=f'''GOAL: {goal}\nEXPECTED: {expected}\nTURN: {turn}\nAVAILABLE VARIABLES (names only): {available}\nPAGE SOURCE XML:\n{redact_text(src,30000)}\nMEMORY:\n{json.dumps(mem,ensure_ascii=False)[:10000]}\nTRACE:\n{json.dumps(trace[-6:],ensure_ascii=False)}'''
        obj,meta=await asyncio.to_thread(complete_json,_system(),prompt,'mobile_agent',700,0.0);action=_validate(obj);providers.append(meta);kind=action['action'];item={'turn':turn,'decision':action,'provider':meta.get('provider'),'model':meta.get('model')}
        try:
            if kind=='fail': item['status']='BLOCKED';trace.append(item);return {'status':'BLOCKED','reason':str(action.get('reason') or 'Bloqueado'),'trace':trace,'providers':providers}
            if kind=='finish':
                ass=action['assertion'];typ=str(ass.get('type') or '')
                if typ!='visible': raise ValueError('Mobile finish solo admite assertion visible en esta versión.')
                await mobile_manager.action(session_id,'expect_visible',**(ass.get('locator') or {}),screenshot=False)
                item['status']='PASS';trace.append(item);return {'status':'PASS','assertion':ass,'trace':trace,'providers':providers}
            cfg=dict(action.get('locator') or {})
            if kind=='fill':
                if action.get('value_from'):
                    key=str(action['value_from'])
                    if key not in variables: raise ValueError(f'Variable no disponible: {key}')
                    cfg['value']=variables[key]
                else: cfg['value']=str(action.get('value') or '')
            elif kind=='wait': cfg={'ms':max(100,min(int(action.get('ms') or 700),10000))}
            await mobile_manager.action(session_id,kind,**cfg,screenshot=False);item['status']='PASS';trace.append(item)
        except Exception as e:
            item['status']='ERROR';item['error']=redact_text(str(e),1000);trace.append(item)
    return {'status':'BLOCKED','reason':'Máximo de acciones Mobile alcanzado sin assertion verificable.','trace':trace,'providers':providers}
