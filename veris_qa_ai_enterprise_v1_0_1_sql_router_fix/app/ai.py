import os
import re
import unicodedata

from .ai_router import complete_text, router_status
from .memory import memory_to_prompt
from .safety import validate_readonly_sql


def _norm(text: str) -> str:
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def _tokens(text: str) -> set[str]:
    words = re.findall(r'[a-zA-Z0-9_]{3,}', _norm(text))
    stop = {
        'que','los','las','una','uno','para','por','con','del','desde','hasta','dame','mostrar','muestra','quiero',
        'datos','tabla','base','consulta','query','sql','todos','todas','ultimo','ultimos','caso','prueba'
    }
    return {w for w in words if w not in stop}


def _memory_text(memory_context: dict | None) -> str:
    if not memory_context: return ''
    parts=[]
    for item in memory_context.get('knowledge',[]): parts.append(item.get('content',''))
    for ex in memory_context.get('examples',[]):
        parts.extend([ex.get('question','') or '',ex.get('sql','') or ''])
    return ' '.join(parts)


def select_relevant_schema(schema: dict, question: str, memory_context: dict | None=None, max_tables: int | None=None) -> dict:
    max_tables=max_tables or int(os.getenv('AI_MAX_SCHEMA_TABLES','20'))
    tables=schema.get('tables',[])
    if len(tables)<=max_tables: return schema
    tokens=_tokens(question+' '+_memory_text(memory_context)); scores=[]
    for table in tables:
        tname=_norm(table.get('name','')); cols=[_norm(c.get('name','')) for c in table.get('columns',[])]
        score=0.0
        for token in tokens:
            if token==tname: score+=10
            elif token in tname: score+=6
            for col in cols:
                if token==col: score+=3
                elif token in col: score+=1
        scores.append((score,table))
    scores.sort(key=lambda x:(-x[0],x[1].get('name','')))
    selected=[t for score,t in scores if score>0][:max_tables]
    if len(selected)<min(10,max_tables):
        names={t.get('name') for t in selected}
        for _,t in scores:
            if t.get('name') not in names:
                selected.append(t); names.add(t.get('name'))
            if len(selected)>=min(10,max_tables): break
    by_name={t.get('name'):t for t in tables}; names={t.get('name') for t in selected}
    for table in list(selected):
        for fk in table.get('foreign_keys',[]):
            ref=fk.get('referred_table')
            if ref and ref in by_name and ref not in names and len(selected)<max_tables:
                selected.append(by_name[ref]); names.add(ref)
    return {'schema':schema.get('schema','public'),'tables':selected[:max_tables]}


def schema_to_prompt(schema: dict) -> str:
    lines=[f"SCHEMA: {schema.get('schema','public')}"]
    for t in schema.get('tables',[]):
        cols=', '.join(f"{c['name']} {c['type']}" for c in t.get('columns',[]))
        lines.append(f"TABLE {t['name']} ({cols})")
        if t.get('primary_key'): lines.append('  PK: '+', '.join(t['primary_key']))
        for fk in t.get('foreign_keys',[]):
            lines.append('  FK: '+','.join(fk.get('columns',[]))+' -> '+f"{fk.get('referred_table')}({','.join(fk.get('referred_columns',[]))})")
    return '\n'.join(lines)


def _build_prompt(question: str, schema: dict, memory_context: dict | None) -> tuple[str,int]:
    compact=select_relevant_schema(schema,question,memory_context)
    prompt=f'''Generate exactly one PostgreSQL read-only query.
Rules:
- Output ONLY SQL. No markdown or explanation.
- READ ONLY: SELECT / WITH / SHOW / EXPLAIN only.
- Use ONLY identifiers present in CURRENT DATABASE SCHEMA.
- Prefer explicit JOINs and PK/FK relationships.
- Never invent a table or column.
- Never use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, CALL, DO, COPY, GRANT or REVOKE.
- Avoid SELECT * when specific columns are enough.
- Unless the user explicitly requests all rows or an aggregate, LIMIT to 200 or less.
- Use PostgreSQL syntax.
- Persistent memory is context only; current schema has priority.

CURRENT DATABASE SCHEMA:
{schema_to_prompt(compact)}

PERSISTENT SQL MEMORY:
{memory_to_prompt(memory_context or {}) or '(none)'}

USER REQUEST:
{question}

SQL:'''
    return prompt,len(compact.get('tables',[]))


def generate_sql(question: str, schema: dict, memory_context: dict | None=None) -> tuple[str,dict]:
    prompt,table_count=_build_prompt(question,schema,memory_context)

    def validate_provider_sql(raw: str) -> str:
        # Un proveedor solo cuenta como exitoso si realmente produjo una
        # sentencia SQL PostgreSQL de solo lectura. Si devuelve texto,
        # instrucciones, markdown o SQL inválido, el AI Router continúa
        # automáticamente con el siguiente proveedor configurado.
        candidate = _clean_sql(raw)
        return validate_readonly_sql(candidate)

    sql,meta=complete_text(
        'You are the SQL specialist inside Veris QA AI. Return one executable PostgreSQL read-only statement and nothing else.',
        prompt,
        purpose='sql_generation',
        max_tokens=600,
        temperature=0,
        validator=validate_provider_sql,
    )
    return sql,{'provider':meta['provider'],'model':meta['model'],'schema_tables_sent':table_count,'attempts':meta.get('attempts',[]),'latency_ms':meta.get('latency_ms')}


def provider_info() -> dict:
    status=router_status(); available=[p for p in status['providers'] if p.get('configured')]
    first=available[0] if available else {'name':'none','model':'none'}
    return {'provider':'router','model':first.get('model','none'),'provider_count':len(available),'order':status['order']}


def _clean_sql(raw: str) -> str:
    """Extrae una única sentencia SQL de respuestas de modelos ruidosas.

    Algunos proveedores gratuitos pueden anteponer frases como "SQL:",
    repetir una instrucción del prompt o envolver la consulta en markdown.
    Este limpiador intenta recuperar solamente la sentencia ejecutable; la
    validación definitiva siempre la realiza ``validate_readonly_sql``.
    """
    text=(raw or '').strip()
    if not text:
        return ''

    # Preferir un bloque ```sql ... ``` si existe.
    fenced=re.search(r'```(?:sql|postgresql)?\s*(.*?)```',text,flags=re.I|re.S)
    if fenced:
        text=fenced.group(1).strip()

    # Quitar etiquetas habituales al principio.
    text=re.sub(r'^\s*(?:SQL|QUERY|POSTGRESQL)\s*:\s*','',text,flags=re.I)

    # Buscar el comienzo de una sentencia SQL en una línea propia. Evitamos
    # capturar menciones de SELECT/WITH dentro de explicaciones del prompt.
    match=re.search(r'(?im)^\s*(SELECT|WITH|SHOW|EXPLAIN)\b',text)
    if match:
        text=text[match.start():].strip()

    # Si el modelo añadió una explicación DESPUÉS de un bloque sin fences,
    # conservar hasta el primer ';'. Para SQL sin ';', la validación decidirá.
    semi=text.find(';')
    if semi >= 0:
        text=text[:semi+1]

    text=re.sub(r'^```(?:sql|postgresql)?\s*','',text,flags=re.I)
    text=re.sub(r'\s*```$','',text)
    return text.strip().rstrip(';')
