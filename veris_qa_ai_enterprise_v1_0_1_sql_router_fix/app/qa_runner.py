import html
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .agent_memory import save_playbook
from .ai import generate_sql
from .api_lab import execute_api_request, json_path_get
from .browser_agent import run_browser_goal
from .browser_manager import browser_manager
from .mobile_agent import run_mobile_goal
from .mobile_manager import mobile_manager
from .connection_store import load_connection
from .control_store import create_execution, finish_execution, save_qa_experience, get_test_case, get_web_profile, get_mobile_profile
from .db import execute_readonly, load_schema
from .memory import latest_schema_snapshot, retrieve_context as retrieve_sql_context, record_query, save_schema, update_query_result
from .models import DbConfig
from .safety import validate_readonly_sql
from .security_utils import audit, redact_text, redact_obj

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / 'reports'
DEFAULT_TIMEOUT_MS = int(os.getenv('STATEMENT_TIMEOUT_MS', '120000'))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve(value, context: dict):
    if isinstance(value, str):
        out=value
        for key,val in context.items(): out=out.replace('{{'+key+'}}',str(val))
        return out
    if isinstance(value,list): return [_resolve(v,context) for v in value]
    if isinstance(value,dict): return {k:_resolve(v,context) for k,v in value.items()}
    return value


def _status_from_steps(steps: list[dict]) -> str:
    if any(s.get('status')=='FAIL' for s in steps): return 'FAIL'
    if any(s.get('status')=='BLOCKED' for s in steps): return 'BLOCKED'
    return 'PASS'


def _cfg_from_connection(connection_id: int) -> DbConfig:
    conn=load_connection(int(connection_id))
    if not conn: raise ValueError('Conexión PostgreSQL guardada no encontrada.')
    return DbConfig(**{k:conn[k] for k in ('host','port','database','user','password','sslmode','schema_name')})


def _rows_as_dicts(data: dict) -> list[dict]:
    cols=data.get('columns') or []
    return [dict(zip(cols,row)) for row in (data.get('rows') or [])]


def _assert_db(data: dict, expect: dict | None) -> None:
    if not expect: return
    count=int(data.get('row_count',0))
    if expect.get('min_rows') is not None and count<int(expect['min_rows']):
        raise AssertionError(f"DB: se esperaban al menos {expect['min_rows']} filas y llegaron {count}.")
    if expect.get('max_rows') is not None and count>int(expect['max_rows']):
        raise AssertionError(f"DB: se esperaban máximo {expect['max_rows']} filas y llegaron {count}.")
    rules=expect.get('first_row') or {}
    if rules:
        rows=_rows_as_dicts(data)
        if not rows: raise AssertionError('DB: no hay primera fila para evaluar las assertions.')
        row=rows[0]
        for col,rule in rules.items():
            if col not in row: raise AssertionError(f'DB: columna esperada no devuelta: {col}')
            actual=row[col]; rule=rule if isinstance(rule,dict) else {'equals':rule}
            if 'equals' in rule and actual!=rule['equals']: raise AssertionError(f'DB {col}: esperado={rule["equals"]!r}, actual={actual!r}')
            if 'not_equals' in rule and actual==rule['not_equals']: raise AssertionError(f'DB {col}: no debía ser {actual!r}')
            if rule.get('is_null') and actual is not None: raise AssertionError(f'DB {col}: se esperaba NULL, actual={actual!r}')
            if rule.get('not_null') and actual is None: raise AssertionError(f'DB {col}: no debía ser NULL.')
            if 'contains' in rule and str(rule['contains']) not in str(actual): raise AssertionError(f'DB {col}: {actual!r} no contiene {rule["contains"]!r}')
            if 'in' in rule and actual not in list(rule['in']): raise AssertionError(f'DB {col}: {actual!r} no está en {rule["in"]!r}')


def _sql_for_question(cfg: DbConfig, question: str) -> tuple[str,dict,int]:
    schema=latest_schema_snapshot(cfg)
    if not schema:
        schema=load_schema(cfg); save_schema(cfg,schema)
    memory_context=retrieve_sql_context(cfg,question)
    raw,info=generate_sql(question,schema,memory_context)
    sql=validate_readonly_sql(raw)
    memory_id=record_query(cfg,question=question,sql=sql,status='generated',provider=info['provider'],model=info['model'],source='qa-agent')
    return sql,info,memory_id


async def run_test_case(test_case_id: int) -> dict:
    case=get_test_case(test_case_id)
    if not case: raise ValueError('Caso de prueba no encontrado.')
    plan=case.get('plan') or {}; steps=plan.get('steps') or []
    execution_id=create_execution(case['id'],case['case_key'],case['name'])
    run_dir=REPORTS_DIR/f'run-{execution_id:06d}'; run_dir.mkdir(parents=True,exist_ok=True); (run_dir/'screenshots').mkdir(exist_ok=True); (run_dir/'trace').mkdir(exist_ok=True)
    started=time.perf_counter(); context=dict(plan.get('variables') or {}); results=[]; browser_session_id=None; mobile_session_id=None
    audit('qa.execution.start',{'execution_id':execution_id,'case_key':case['case_key'],'steps':len(steps)})

    try:
        for index,raw_step in enumerate(steps,start=1):
            step=_resolve(raw_step,context); kind=str(step.get('type') or '').strip(); name=step.get('name') or kind or f'Paso {index}'
            step_started=time.perf_counter(); result={'index':index,'type':kind,'name':name,'status':'PASS','started_at':_now()}
            try:
                if kind in {'db.query','db.ai_query'}:
                    cfg=_cfg_from_connection(int(step['connection_id']))
                    memory_id=None; info=None
                    if kind=='db.query':
                        sql=validate_readonly_sql(str(step.get('sql') or ''))
                    else:
                        question=str(step.get('question') or step.get('goal') or '').strip()
                        if not question: raise ValueError('db.ai_query necesita question.')
                        sql,info,memory_id=_sql_for_question(cfg,question)
                    data=execute_readonly(cfg,sql,int(step.get('max_rows',100)),int(step.get('timeout_ms',DEFAULT_TIMEOUT_MS)))
                    result['data']={**data,'sql':sql}
                    if info: result['ai']={'provider':info.get('provider'),'model':info.get('model'),'attempts':info.get('attempts',[])}
                    if memory_id: update_query_result(memory_id,status='success',row_count=data.get('row_count'))
                    if step.get('save_first_row_as') and data.get('rows'):
                        row_obj=_rows_as_dicts(data)[0]; prefix=str(step['save_first_row_as'])
                        for k,v in row_obj.items(): context[f'{prefix}.{k}']=v
                    if 'expect_min_rows' in step and data.get('row_count',0)<int(step['expect_min_rows']):
                        raise AssertionError(f"Se esperaban al menos {step['expect_min_rows']} filas y llegaron {data.get('row_count',0)}.")
                    _assert_db(data,step.get('expect'))

                elif kind=='api.request':
                    data=await execute_api_request(profile_id=int(step['profile_id']),method=str(step.get('method') or 'GET'),path=str(step.get('path') or ''),headers=step.get('headers') or {},body=step.get('body'),params=step.get('params') or {})
                    result['data']=data; expected_status=step.get('expected_status')
                    if expected_status is not None and int(data['status_code'])!=int(expected_status): raise AssertionError(f'HTTP esperado {expected_status}, recibido {data["status_code"]}.')
                    if step.get('json_path'):
                        actual=json_path_get(data.get('body'),str(step['json_path'])); result['json_path_value']=actual
                        if 'equals' in step and actual!=step['equals']: raise AssertionError(f"{step['json_path']} esperado={step['equals']!r}, actual={actual!r}")
                    if step.get('save_json_path_as'):
                        actual=json_path_get(data.get('body'),str(step.get('json_path') or '')); context[str(step['save_json_path_as'])]=actual

                elif kind=='mobile.start':
                    profile=get_mobile_profile(int(step['profile_id']),include_secret=True)
                    if not profile: raise ValueError('Perfil Mobile no encontrado.')
                    data=await mobile_manager.start(profile); mobile_session_id=data['session_id']
                    context['mobile.platform']=profile.get('platform',''); context['mobile.device_name']=profile.get('device_name',''); context['mobile.username']=profile.get('username',''); context['mobile.password']=profile.get('password','')
                    result['data']={k:v for k,v in data.items() if k!='data_url'}
                    if step.get('evidence'):
                        filename=f'{index:02d}-{str(name).replace(" ","_")}.png'; shot=await mobile_manager.screenshot(mobile_session_id,str(run_dir/'screenshots'/filename)); result['evidence']=shot.get('path')

                elif kind=='mobile.agent':
                    if not mobile_session_id: raise RuntimeError('No hay sesión Mobile activa. Agrega mobile.start antes de mobile.agent.')
                    data=await run_mobile_goal(mobile_session_id,str(step.get('goal') or name),str(step.get('expected') or ''),variables=context,max_actions=int(step.get('max_actions',12)))
                    result['data']=data; result['status']=data.get('status','BLOCKED')
                    if step.get('evidence'):
                        filename=f'{index:02d}-{str(name).replace(" ","_")}.png'; shot=await mobile_manager.screenshot(mobile_session_id,str(run_dir/'screenshots'/filename)); result['evidence']=shot.get('path')

                elif kind.startswith('mobile.'):
                    if not mobile_session_id: raise RuntimeError('No hay sesión Mobile activa. Agrega mobile.start antes de acciones mobile.')
                    action=kind.split('.',1)[1]
                    if action=='stop':
                        data=await mobile_manager.stop(mobile_session_id); mobile_session_id=None
                    else:
                        cfg={k:v for k,v in step.items() if k not in {'type','name','evidence'}}; cfg['screenshot']=False
                        data=await mobile_manager.action(mobile_session_id,action,**cfg)
                        if action=='screenshot' or step.get('evidence'):
                            filename=f'{index:02d}-{str(name).replace(" ","_")}.png'; shot=await mobile_manager.screenshot(mobile_session_id,str(run_dir/'screenshots'/filename)); data['screenshot_path']=shot.get('path')
                        data.pop('data_url',None)
                    result['data']=data

                elif kind=='web.start':
                    profile=get_web_profile(int(step['profile_id']),include_secret=True)
                    if not profile: raise ValueError('Perfil Web no encontrado.')
                    data=await browser_manager.start(profile,trace_path=str(run_dir/'trace'/'playwright-trace.zip')); browser_session_id=data['session_id']
                    context['web.username']=profile.get('username',''); context['web.password']=profile.get('password',''); context['web.base_url']=profile.get('base_url',''); context['web.login_url']=profile.get('login_url','')
                    result['data']={k:v for k,v in data.items() if k!='data_url'}

                elif kind=='web.agent':
                    if not browser_session_id: raise RuntimeError('No hay sesión Web activa. Agrega web.start antes de web.agent.')
                    data=await run_browser_goal(browser_session_id,str(step.get('goal') or name),str(step.get('expected') or ''),variables=context,max_actions=int(step.get('max_actions',12)))
                    result['data']=data
                    result['status']=data.get('status','BLOCKED')
                    if step.get('evidence'):
                        filename=f'{index:02d}-{str(name).replace(" ","_")}.png'; shot=await browser_manager.screenshot(browser_session_id,str(run_dir/'screenshots'/filename)); result['evidence']=shot.get('path')

                elif kind.startswith('web.'):
                    if not browser_session_id: raise RuntimeError('No hay sesión Web activa. Agrega web.start antes de acciones web.')
                    action=kind.split('.',1)[1]
                    if action=='stop':
                        data=await browser_manager.stop(browser_session_id); browser_session_id=None
                    else:
                        cfg={k:v for k,v in step.items() if k not in {'type','name','evidence'}}; cfg['screenshot']=False
                        data=await browser_manager.action(browser_session_id,action,**cfg)
                        if action=='screenshot' or step.get('evidence'):
                            filename=f'{index:02d}-{str(name).replace(" ","_")}.png'; shot=await browser_manager.screenshot(browser_session_id,str(run_dir/'screenshots'/filename)); data['screenshot_path']=shot.get('path')
                        data.pop('data_url',None)
                    result['data']=data

                elif kind=='wait':
                    import asyncio
                    await asyncio.sleep(max(0,int(step.get('ms',500)))/1000); result['data']={'waited_ms':int(step.get('ms',500))}
                elif kind=='assert.equals':
                    left=step.get('left'); right=step.get('right')
                    if left!=right: raise AssertionError(f'Valores distintos: {left!r} != {right!r}')
                    result['data']={'left':left,'right':right}
                else:
                    raise ValueError(f'Tipo de paso no soportado: {kind}')

            except AssertionError as exc:
                result['status']='FAIL'; result['error']=str(exc)
            except Exception as exc:
                result['status']='FAIL'; result['error']=redact_text(str(exc),3000)

            result['duration_ms']=round((time.perf_counter()-step_started)*1000,1); results.append(result)
            audit('qa.step.finish',{'execution_id':execution_id,'index':index,'type':kind,'status':result['status'],'duration_ms':result['duration_ms'],'error':result.get('error','')})
            if result['status'] in {'FAIL','BLOCKED'} and not bool(step.get('continue_on_fail',False)): break
    finally:
        if browser_session_id:
            try: await browser_manager.stop(browser_session_id)
            except Exception: pass
        if mobile_session_id:
            try: await mobile_manager.stop(mobile_session_id)
            except Exception: pass

    duration_ms=round((time.perf_counter()-started)*1000,1); status=_status_from_steps(results)
    persisted_steps = redact_obj(results) if os.getenv('REPORT_REDACT_PII','true').strip().lower() in {'1','true','yes','on','si','sí'} else results
    trace_file=run_dir/'trace'/'playwright-trace.zip'
    report={'execution_id':execution_id,'case_id':case['id'],'case_key':case['case_key'],'case_name':case['name'],'status':status,'duration_ms':duration_ms,'started_at':results[0]['started_at'] if results else _now(),'finished_at':_now(),'steps':persisted_steps,'context_keys':sorted(context.keys()),'source':redact_obj(plan.get('source') or {}),'artifacts':{'playwright_trace':str(trace_file) if trace_file.exists() else None,'screenshots_dir':str(run_dir/'screenshots')}}
    json_path=run_dir/'report.json'; html_path=run_dir/'report.html'; json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); html_path.write_text(_render_html_report(report),encoding='utf-8')
    finish_execution(execution_id,status,duration_ms,report,str(json_path),str(html_path)); experience_id=save_qa_experience(execution_id,report)
    failed=[{'type':x.get('type'),'name':x.get('name'),'error':x.get('error')} for x in results if x.get('status')!='PASS']
    playbook_id=save_playbook(case['case_key'],case['name'],status,plan,{'failed_steps':failed,'duration_ms':duration_ms,'successful_step_types':sorted({x.get('type') for x in results if x.get('status')=='PASS'})},verified=False)
    audit('qa.execution.finish',{'execution_id':execution_id,'case_key':case['case_key'],'status':status,'duration_ms':duration_ms,'experience_id':experience_id,'playbook_id':playbook_id})
    return {**report,'experience_id':experience_id,'playbook_id':playbook_id,'report_json':str(json_path),'report_html':str(html_path)}


def _render_html_report(report: dict) -> str:
    rows=[]
    for step in report.get('steps',[]):
        rows.append('<tr>'+f"<td>{step.get('index')}</td>"+f"<td>{html.escape(str(step.get('name')))}</td>"+f"<td>{html.escape(str(step.get('type')))}</td>"+f"<td class='{str(step.get('status','')).lower()}'>{html.escape(str(step.get('status')))}</td>"+f"<td>{step.get('duration_ms',0)} ms</td>"+f"<td><pre>{html.escape(step.get('error',''))}</pre></td>"+'</tr>')
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'><title>{html.escape(report['case_key'])}</title>
<style>body{{font-family:Arial,sans-serif;background:#0b1220;color:#e5edf8;padding:32px}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #334155;padding:10px;text-align:left;vertical-align:top}}.pass{{color:#34d399}}.fail{{color:#fb7185}}.blocked{{color:#fbbf24}}pre{{white-space:pre-wrap}}</style></head><body>
<h1>Veris QA AI — {html.escape(report['case_key'])}</h1><p>{html.escape(report['case_name'])}</p><p>Estado: <strong>{html.escape(report['status'])}</strong> · {report['duration_ms']} ms</p>
<table><thead><tr><th>#</th><th>Paso</th><th>Tipo</th><th>Estado</th><th>Tiempo</th><th>Error</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""


def environment_check() -> dict:
    commands={'python':shutil.which('python') or shutil.which('python3'),'node':shutil.which('node'),'appium':shutil.which('appium'),'adb':shutil.which('adb'),'xcodebuild':shutil.which('xcodebuild')}
    return {'commands':{k:{'ok':bool(v),'path':v} for k,v in commands.items()},'playwright_python':browser_manager.available(),'platform':os.name}
