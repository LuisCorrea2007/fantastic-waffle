from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import httpx

BASE_DIR=Path(__file__).resolve().parent.parent
SCREENSHOT_DIR=BASE_DIR/'data'/'mobile_screenshots'
W3C_ELEMENT='element-6066-11e4-a52e-4f735466cecf'


@dataclass
class MobileSession:
    local_id: str
    remote_id: str
    profile: dict
    client: httpx.AsyncClient
    started_at: float


class MobileManager:
    def __init__(self):
        self.sessions: dict[str,MobileSession]={}
        self.lock=asyncio.Lock()

    def _caps(self,p:dict)->dict:
        platform=(p.get('platform') or 'android').lower()
        caps={
            'platformName':'Android' if platform=='android' else 'iOS',
            'appium:automationName':p.get('automation_name') or ('UiAutomator2' if platform=='android' else 'XCUITest'),
            'appium:deviceName':p.get('device_name') or ('Android' if platform=='android' else 'iPhone'),
            'appium:newCommandTimeout':120,
        }
        if p.get('udid'): caps['appium:udid']=p['udid']
        if p.get('platform_version'): caps['appium:platformVersion']=p['platform_version']
        if p.get('app_path'): caps['appium:app']=p['app_path']
        if platform=='android':
            if p.get('app_package'): caps['appium:appPackage']=p['app_package']
            if p.get('app_activity'): caps['appium:appActivity']=p['app_activity']
            caps['appium:autoGrantPermissions']=True
        else:
            if p.get('bundle_id'): caps['appium:bundleId']=p['bundle_id']
        return caps

    async def start(self,profile:dict)->dict:
        base=(profile.get('appium_url') or 'http://127.0.0.1:4723').rstrip('/')
        timeout=max(5.0,float(profile.get('timeout_ms',30000))/1000)
        client=httpx.AsyncClient(base_url=base,timeout=httpx.Timeout(timeout,connect=min(8,timeout)))
        try:
            r=await client.post('/session',json={'capabilities':{'alwaysMatch':self._caps(profile),'firstMatch':[{}]}})
            r.raise_for_status(); payload=r.json(); value=payload.get('value') or {}
            remote=value.get('sessionId') or payload.get('sessionId')
            if not remote: raise RuntimeError(f'Appium no devolvió sessionId: {payload}')
            local=uuid.uuid4().hex; s=MobileSession(local,str(remote),profile,client,time.time())
            async with self.lock: self.sessions[local]=s
            return await self.status(local,include_screenshot=True)
        except Exception:
            await client.aclose(); raise

    def _get(self,sid:str)->MobileSession:
        s=self.sessions.get(sid)
        if not s: raise ValueError('Sesión Mobile no encontrada o cerrada.')
        return s

    async def _req(self,s:MobileSession,method:str,path:str,**kwargs):
        r=await s.client.request(method,path,**kwargs)
        if r.status_code>=400: raise RuntimeError(f'Appium HTTP {r.status_code}: {r.text[:1000]}')
        data=r.json() if r.content else {'value':None}
        value=data.get('value')
        if isinstance(value,dict) and value.get('error'):
            raise RuntimeError(f"Appium {value.get('error')}: {value.get('message','')}")
        return value

    def _strategy(self,s:MobileSession,cfg:dict)->tuple[str,str]:
        for key,strategy in [('accessibility_id','accessibility id'),('id','id'),('xpath','xpath'),('class_name','class name'),('android_uiautomator','-android uiautomator'),('ios_predicate','-ios predicate string')]:
            if cfg.get(key): return strategy,str(cfg[key])
        if cfg.get('text'):
            t=str(cfg['text']).replace('"','\\"')
            if (s.profile.get('platform') or '').lower()=='ios': return 'xpath',f'//*[@label="{t}" or @name="{t}" or @value="{t}"]'
            return 'xpath',f'//*[@text="{t}" or @content-desc="{t}"]'
        raise ValueError('Mobile necesita accessibility_id, id, xpath, class_name, android_uiautomator, ios_predicate o text.')

    async def _element(self,s:MobileSession,cfg:dict)->str:
        using,value=self._strategy(s,cfg)
        data=await self._req(s,'POST',f'/session/{s.remote_id}/element',json={'using':using,'value':value})
        if not isinstance(data,dict): raise RuntimeError('Appium no devolvió elemento.')
        eid=data.get(W3C_ELEMENT) or data.get('ELEMENT')
        if not eid: raise RuntimeError('Appium no devolvió element id.')
        return str(eid)

    async def action(self,session_id:str,action:str,**cfg)->dict:
        s=self._get(session_id); a=action.lower().strip(); started=time.perf_counter()
        if a in {'click','fill','expect_visible'}:
            eid=await self._element(s,cfg)
            if a=='click': await self._req(s,'POST',f'/session/{s.remote_id}/element/{eid}/click',json={})
            elif a=='fill':
                value=str(cfg.get('value',''))
                await self._req(s,'POST',f'/session/{s.remote_id}/element/{eid}/clear',json={})
                await self._req(s,'POST',f'/session/{s.remote_id}/element/{eid}/value',json={'text':value,'value':list(value)})
        elif a=='back': await self._req(s,'POST',f'/session/{s.remote_id}/back',json={})
        elif a=='wait': await asyncio.sleep(max(0,int(cfg.get('ms',500)))/1000)
        elif a=='screenshot': pass
        else: raise ValueError(f'Acción Mobile no soportada: {a}')
        out=await self.status(session_id,include_screenshot=bool(cfg.get('screenshot',True)));out['action']=a;out['action_elapsed_ms']=round((time.perf_counter()-started)*1000,1);return out

    async def source(self,session_id:str,max_chars:int=40000)->str:
        s=self._get(session_id); src=await self._req(s,'GET',f'/session/{s.remote_id}/source');return str(src or '')[:max_chars]

    async def screenshot(self,session_id:str,save_name:str|None=None)->dict:
        s=self._get(session_id); encoded=await self._req(s,'GET',f'/session/{s.remote_id}/screenshot'); raw=base64.b64decode(encoded or '') if encoded else b''
        out={'data_url':'data:image/png;base64,'+(encoded or '')}
        if save_name:
            path=Path(save_name); path.parent.mkdir(parents=True,exist_ok=True)
            if path.suffix.lower()!='.png': path=path.with_suffix('.png')
            path.write_bytes(raw);out['path']=str(path)
        return out

    async def status(self,session_id:str,include_screenshot:bool=False)->dict:
        s=self._get(session_id);out={'session_id':session_id,'remote_session_id':s.remote_id,'platform':s.profile.get('platform'),'device_name':s.profile.get('device_name'),'appium_url':s.profile.get('appium_url')}
        if include_screenshot:
            try: out.update(await self.screenshot(session_id))
            except Exception as e: out['screenshot_error']=str(e)
        return out

    async def stop(self,session_id:str)->dict:
        async with self.lock: s=self.sessions.pop(session_id,None)
        if not s:return {'ok':True,'already_closed':True}
        try: await self._req(s,'DELETE',f'/session/{s.remote_id}')
        except Exception: pass
        await s.client.aclose();return {'ok':True}


mobile_manager=MobileManager()
