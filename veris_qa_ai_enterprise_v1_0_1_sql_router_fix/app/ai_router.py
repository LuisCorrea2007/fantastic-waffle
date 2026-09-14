from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, asdict
from typing import Callable, Any

import httpx

from .agent_memory import record_agent_run
from .security_utils import audit, redact_text


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    api_key: str
    base_url: str
    kind: str  # openai | gemini
    enabled: bool = True


_STATE_LOCK = threading.Lock()
_PROVIDER_STATE: dict[str, dict[str, Any]] = {}


def _bool_env(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1','true','yes','on','si','sí'}


def _provider_catalog() -> dict[str, ProviderConfig]:
    return {
        'openrouter': ProviderConfig(
            'openrouter', os.getenv('OPENROUTER_MODEL','openrouter/free').strip(), os.getenv('OPENROUTER_API_KEY','').strip(),
            os.getenv('OPENROUTER_BASE_URL','https://openrouter.ai/api/v1').rstrip('/'), 'openai',
        ),
        'groq': ProviderConfig(
            'groq', os.getenv('GROQ_MODEL','openai/gpt-oss-20b').strip(), os.getenv('GROQ_API_KEY','').strip(),
            os.getenv('GROQ_BASE_URL','https://api.groq.com/openai/v1').rstrip('/'), 'openai',
        ),
        'gemini': ProviderConfig(
            'gemini', os.getenv('GEMINI_MODEL','gemini-3.5-flash').strip(), os.getenv('GEMINI_API_KEY','').strip(),
            os.getenv('GEMINI_BASE_URL','https://generativelanguage.googleapis.com/v1beta').rstrip('/'), 'gemini',
        ),
        'mistral': ProviderConfig(
            'mistral', os.getenv('MISTRAL_MODEL','mistral-small-latest').strip(), os.getenv('MISTRAL_API_KEY','').strip(),
            os.getenv('MISTRAL_BASE_URL','https://api.mistral.ai/v1').rstrip('/'), 'openai',
        ),
        'compatible': ProviderConfig(
            'compatible', os.getenv('AI_COMPAT_MODEL','').strip(), os.getenv('AI_COMPAT_API_KEY','').strip(),
            os.getenv('AI_COMPAT_BASE_URL','').rstrip('/'), 'openai',
        ),
    }


def provider_order() -> list[str]:
    raw = os.getenv('AI_PROVIDER_ORDER','openrouter,groq,gemini,mistral')
    valid = _provider_catalog()
    result=[]
    for item in raw.split(','):
        key=item.strip().lower()
        if key in valid and key not in result:
            result.append(key)
    return result


def configured_providers() -> list[ProviderConfig]:
    catalog=_provider_catalog(); out=[]
    for name in provider_order():
        p=catalog[name]
        if p.enabled and p.api_key and p.model and p.base_url:
            out.append(p)
    return out


def _state(name: str) -> dict[str, Any]:
    with _STATE_LOCK:
        return dict(_PROVIDER_STATE.get(name, {'consecutive_failures':0,'cooldown_until':0.0,'last_error':'','last_ok':None,'last_latency_ms':None}))


def _set_success(name: str, latency_ms: float) -> None:
    with _STATE_LOCK:
        _PROVIDER_STATE[name]={'consecutive_failures':0,'cooldown_until':0.0,'last_error':'','last_ok':time.time(),'last_latency_ms':round(latency_ms,1)}


def _set_failure(name: str, error: str, retry_after: float | None = None, force_cooldown: bool = False) -> None:
    threshold=max(1,int(os.getenv('AI_CIRCUIT_FAILURES','2')))
    base=max(10,float(os.getenv('AI_CIRCUIT_COOLDOWN_SECONDS','90')))
    with _STATE_LOCK:
        s=_PROVIDER_STATE.setdefault(name, {'consecutive_failures':0,'cooldown_until':0.0,'last_error':'','last_ok':None,'last_latency_ms':None})
        s['consecutive_failures']=int(s.get('consecutive_failures',0))+1
        s['last_error']=redact_text(error,500)
        if force_cooldown or s['consecutive_failures'] >= threshold:
            s['cooldown_until']=time.time()+max(base,float(retry_after or 0))


def router_status() -> dict:
    catalog=_provider_catalog(); now=time.time(); items=[]
    for name in provider_order():
        p=catalog[name]; s=_state(name)
        items.append({
            'name':name,'model':p.model,'configured':bool(p.api_key and p.model and p.base_url),'base_url':p.base_url,
            'available_now':bool(p.api_key and p.model and p.base_url and float(s.get('cooldown_until',0))<=now),
            'cooldown_seconds':max(0,round(float(s.get('cooldown_until',0))-now,1)),
            'consecutive_failures':s.get('consecutive_failures',0),'last_error':s.get('last_error',''),
            'last_latency_ms':s.get('last_latency_ms'),
        })
    return {'order':provider_order(),'configured_count':sum(1 for x in items if x['configured']),'providers':items,'redaction_enabled':_bool_env('AI_REDACT_BEFORE_SEND',True)}


def _timeout() -> httpx.Timeout:
    total=max(10.0,float(os.getenv('AI_REQUEST_TIMEOUT_SECONDS','60')))
    return httpx.Timeout(connect=min(10,total),read=total,write=min(30,total),pool=10)


def _extract_openai_text(data: dict) -> str:
    choices=data.get('choices') or []
    if not choices:
        raise RuntimeError('Respuesta sin choices.')
    content=(choices[0].get('message') or {}).get('content')
    if isinstance(content,str): return content.strip()
    if isinstance(content,list):
        parts=[]
        for item in content:
            if isinstance(item,dict):
                if item.get('text'): parts.append(str(item['text']))
                elif item.get('type')=='text' and item.get('content'): parts.append(str(item['content']))
        return '\n'.join(parts).strip()
    return str(content or '').strip()


def _call_openai_compatible(p: ProviderConfig, system: str, user: str, max_tokens: int, temperature: float, json_mode: bool) -> str:
    headers={'Authorization':f'Bearer {p.api_key}','Content-Type':'application/json'}
    if p.name=='openrouter':
        headers['X-Title']=os.getenv('OPENROUTER_APP_TITLE','Veris QA AI')
        referer=os.getenv('OPENROUTER_HTTP_REFERER','').strip()
        if referer: headers['HTTP-Referer']=referer
    body={
        'model':p.model,
        'messages':[{'role':'system','content':system},{'role':'user','content':user}],
        'temperature':temperature,
        'max_tokens':max_tokens,
    }
    if json_mode and p.name in {'openrouter','mistral'}:
        body['response_format']={'type':'json_object'}
    with httpx.Client(timeout=_timeout()) as client:
        r=client.post(f'{p.base_url}/chat/completions',headers=headers,json=body)
        if r.status_code>=400:
            detail=redact_text(r.text,800)
            err=RuntimeError(f'HTTP {r.status_code}: {detail}')
            setattr(err,'status_code',r.status_code); setattr(err,'headers',dict(r.headers)); raise err
        return _extract_openai_text(r.json())


def _call_gemini(p: ProviderConfig, system: str, user: str, max_tokens: int, temperature: float, json_mode: bool) -> str:
    headers={'x-goog-api-key':p.api_key,'Content-Type':'application/json'}
    body={
        'systemInstruction':{'parts':[{'text':system}]},
        'contents':[{'role':'user','parts':[{'text':user}]}],
        'generationConfig':{'temperature':temperature,'maxOutputTokens':max_tokens},
    }
    if json_mode:
        body['generationConfig']['responseMimeType']='application/json'
    model=p.model
    if model.startswith('models/'): model=model.split('/',1)[1]
    with httpx.Client(timeout=_timeout()) as client:
        r=client.post(f'{p.base_url}/models/{model}:generateContent',headers=headers,json=body)
        if r.status_code>=400:
            detail=redact_text(r.text,800)
            err=RuntimeError(f'HTTP {r.status_code}: {detail}')
            setattr(err,'status_code',r.status_code); setattr(err,'headers',dict(r.headers)); raise err
        data=r.json()
    candidates=data.get('candidates') or []
    if not candidates: raise RuntimeError('Gemini devolvió una respuesta sin candidates.')
    parts=((candidates[0].get('content') or {}).get('parts') or [])
    text='\n'.join(str(x.get('text','')) for x in parts if isinstance(x,dict) and x.get('text')).strip()
    if not text: raise RuntimeError('Gemini no devolvió texto.')
    return text


def _parse_retry_after(exc: Exception) -> float | None:
    headers=getattr(exc,'headers',{}) or {}
    raw=headers.get('retry-after') or headers.get('Retry-After')
    try: return float(raw)
    except Exception: return None


def _clean_for_provider(text: str) -> str:
    if _bool_env('AI_REDACT_BEFORE_SEND',True):
        return redact_text(text, int(os.getenv('AI_MAX_PROMPT_CHARS','80000')))
    limit=int(os.getenv('AI_MAX_PROMPT_CHARS','80000'))
    return text[:limit]


def _json_from_text(raw: str):
    text=(raw or '').strip()
    if text.startswith('```'):
        text=re.sub(r'^```(?:json)?\s*','',text,flags=re.I)
        text=re.sub(r'\s*```$','',text)
    try: return json.loads(text)
    except Exception: pass
    starts=[i for i,c in enumerate(text) if c in '[{']
    for start in starts:
        opener=text[start]; closer='}' if opener=='{' else ']'; depth=0; in_str=False; esc=False
        for i in range(start,len(text)):
            ch=text[i]
            if in_str:
                if esc: esc=False
                elif ch=='\\': esc=True
                elif ch=='"': in_str=False
                continue
            if ch=='"': in_str=True; continue
            if ch==opener: depth+=1
            elif ch==closer:
                depth-=1
                if depth==0:
                    snippet=text[start:i+1]
                    try: return json.loads(snippet)
                    except Exception: break
    raise ValueError('El proveedor no devolvió JSON válido.')


def _route(system: str, user: str, purpose: str, max_tokens: int, temperature: float, json_mode: bool, validator: Callable[[str],Any] | None = None) -> tuple[Any,dict]:
    providers=configured_providers()
    if not providers:
        raise RuntimeError('No hay proveedores IA configurados. Agrega al menos una key en .env: OPENROUTER_API_KEY, GROQ_API_KEY, GEMINI_API_KEY o MISTRAL_API_KEY.')
    system=_clean_for_provider(system); user=_clean_for_provider(user)
    attempts=[]; now=time.time()
    for p in providers:
        s=_state(p.name)
        if float(s.get('cooldown_until',0))>now:
            attempts.append({'provider':p.name,'model':p.model,'status':'cooldown','retry_in_s':round(float(s['cooldown_until'])-now,1)})
            continue
        started=time.perf_counter()
        try:
            if p.kind=='gemini': raw=_call_gemini(p,system,user,max_tokens,temperature,json_mode)
            else: raw=_call_openai_compatible(p,system,user,max_tokens,temperature,json_mode)
            value=validator(raw) if validator else raw
            latency=(time.perf_counter()-started)*1000
            _set_success(p.name,latency)
            meta={'provider':p.name,'model':p.model,'latency_ms':round(latency,1),'attempts':attempts+[{'provider':p.name,'model':p.model,'status':'ok','latency_ms':round(latency,1)}]}
            record_agent_run(purpose,p.name,p.model,True,user[:2000],str(value)[:4000],metadata={'latency_ms':latency,'attempts':meta['attempts']})
            audit('ai.request.ok',{'purpose':purpose,'provider':p.name,'model':p.model,'latency_ms':latency})
            return value,meta
        except Exception as exc:
            latency=(time.perf_counter()-started)*1000
            status=getattr(exc,'status_code',None); retry=_parse_retry_after(exc)
            _set_failure(p.name,str(exc),retry,force_cooldown=status in {401,403,429})
            attempts.append({'provider':p.name,'model':p.model,'status':'error','http_status':status,'latency_ms':round(latency,1),'error':redact_text(str(exc),500)})
            audit('ai.request.fail',{'purpose':purpose,'provider':p.name,'model':p.model,'error':str(exc),'status':status})
            continue
    record_agent_run(purpose,None,None,False,user[:2000],'',error='Todos los proveedores fallaron.',metadata={'attempts':attempts})
    detail=' | '.join(f"{a['provider']}: {a.get('error') or a.get('status')}" for a in attempts)
    raise RuntimeError(f'No fue posible completar la solicitud con ningún proveedor IA. {detail}')


def complete_text(system: str, user: str, purpose: str='general', max_tokens: int=1000, temperature: float=0.0, validator: Callable[[str],Any] | None = None) -> tuple[Any,dict]:
    """
    Completa texto usando el router con failover.

    Si se proporciona ``validator``, la respuesta del proveedor NO se
    considera exitosa hasta que el validador la acepte. Esto permite que
    tareas estructuradas como generación SQL fallen al siguiente proveedor
    cuando un modelo devuelve explicaciones, markdown o texto no ejecutable.
    """
    return _route(system,user,purpose,max_tokens,temperature,False,validator=validator)


def complete_json(system: str, user: str, purpose: str='json', max_tokens: int=1800, temperature: float=0.0) -> tuple[Any,dict]:
    return _route(system,user,purpose,max_tokens,temperature,True,validator=_json_from_text)


def probe_providers() -> dict:
    results=[]
    catalog={p.name:p for p in configured_providers()}
    for name in provider_order():
        if name not in catalog:
            results.append({'provider':name,'configured':False,'ok':False,'detail':'Sin API key/modelo.'}); continue
        p=catalog[name]
        started=time.perf_counter()
        try:
            if p.kind=='gemini': out=_call_gemini(p,'Responde de forma exacta.','Responde solamente: OK',8,0,False)
            else: out=_call_openai_compatible(p,'Responde de forma exacta.','Responde solamente: OK',8,0,False)
            latency=(time.perf_counter()-started)*1000; _set_success(name,latency)
            results.append({'provider':name,'model':p.model,'configured':True,'ok':True,'latency_ms':round(latency,1),'response':redact_text(out,80)})
        except Exception as e:
            latency=(time.perf_counter()-started)*1000; _set_failure(name,str(e),_parse_retry_after(e))
            results.append({'provider':name,'model':p.model,'configured':True,'ok':False,'latency_ms':round(latency,1),'detail':redact_text(str(e),500)})
    return {'providers':results}
