from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_XLSX_EXPANDED_BYTES = 60 * 1024 * 1024
MAX_IMPORT_ROWS = 5000


def _norm_header(value: str) -> str:
    s=(value or '').strip().lower()
    s=re.sub(r'\s+',' ',s)
    replacements={'á':'a','é':'e','í':'i','ó':'o','ú':'u','ñ':'n'}
    for a,b in replacements.items(): s=s.replace(a,b)
    return s


def _cell_text(v) -> str:
    if v is None: return ''
    if isinstance(v,bool): return 'true' if v else 'false'
    return str(v).strip()


def _xlsx_rows(data: bytes) -> list[dict]:
    # Lector XLSX read-only implementado con stdlib para no depender de Excel/Office.
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos=z.infolist()
        if sum(max(0,i.file_size) for i in infos) > MAX_XLSX_EXPANDED_BYTES:
            raise ValueError('El XLSX se expande a más de 60 MB y fue bloqueado por seguridad.')
        names={i.filename for i in infos}
        shared=[]
        if 'xl/sharedStrings.xml' in names:
            root=ET.fromstring(z.read('xl/sharedStrings.xml'))
            ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            for si in root.findall('x:si',ns):
                texts=[]
                for t in si.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t'):
                    texts.append(t.text or '')
                shared.append(''.join(texts))

        # Resolve first visible worksheet through workbook relationships.
        wb=ET.fromstring(z.read('xl/workbook.xml'))
        ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
        sheet=wb.find('x:sheets/x:sheet',ns)
        if sheet is None: return []
        rid=sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target=None
        for rel in rels:
            if rel.attrib.get('Id')==rid:
                target=rel.attrib.get('Target'); break
        if not target: return []
        if target.startswith('/'): path=target.lstrip('/')
        else: path='xl/'+target.lstrip('/')
        path=str(Path(path)).replace('\\','/')
        if path not in names or not path.startswith('xl/'):
            raise ValueError('Estructura XLSX no válida o worksheet no encontrada.')
        root=ET.fromstring(z.read(path))
        main='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

        rows=[]
        for row in root.iter(main+'row'):
            values={}
            for c in row.findall(main+'c'):
                ref=c.attrib.get('r','A1')
                col=re.match(r'([A-Z]+)',ref)
                if not col: continue
                col=col.group(1)
                typ=c.attrib.get('t')
                v=c.find(main+'v')
                inline=c.find(main+'is')
                value=''
                if typ=='inlineStr' and inline is not None:
                    value=''.join((t.text or '') for t in inline.iter(main+'t'))
                elif v is not None:
                    raw=v.text or ''
                    if typ=='s':
                        try: value=shared[int(raw)]
                        except Exception: value=raw
                    elif typ=='b': value='true' if raw=='1' else 'false'
                    else: value=raw
                values[col]=value
            rows.append(values)
        if not rows: return []
        cols=sorted({c for r in rows for c in r.keys()},key=_excel_col_num)
        headers=[_cell_text(rows[0].get(c,'')) or c for c in cols]
        output=[]
        for r in rows[1:]:
            obj={headers[i]:_cell_text(r.get(c,'')) for i,c in enumerate(cols)}
            if any(v for v in obj.values()): output.append(obj)
            if len(output) >= MAX_IMPORT_ROWS: break
        return output


def _excel_col_num(col: str) -> int:
    n=0
    for ch in col: n=n*26+(ord(ch)-64)
    return n


def _csv_rows(data: bytes) -> list[dict]:
    text=data.decode('utf-8-sig',errors='replace')
    sample=text[:4000]
    try: dialect=csv.Sniffer().sniff(sample,delimiters=',;\t|')
    except Exception: dialect=csv.excel
    return [{str(k or '').strip():_cell_text(v) for k,v in row.items()} for _,row in zip(range(MAX_IMPORT_ROWS),csv.DictReader(io.StringIO(text),dialect=dialect))]


def _json_rows(data: bytes):
    parsed=json.loads(data.decode('utf-8-sig'))
    if isinstance(parsed,dict):
        for key in ('cases','test_cases','casos','items','rows','data'):
            if isinstance(parsed.get(key),list): return parsed[key],parsed
        return [parsed],parsed
    if isinstance(parsed,list): return parsed,parsed
    raise ValueError('El JSON debe contener un objeto o lista de casos.')


def parse_uploaded_case_file(filename: str, data: bytes) -> dict:
    if not data: raise ValueError('El archivo está vacío.')
    if len(data)>MAX_UPLOAD_BYTES: raise ValueError('El archivo supera el límite de 10 MB.')
    suffix=Path(filename or '').suffix.lower()
    if suffix=='.xlsx':
        rows=_xlsx_rows(data); raw=rows; source_type='xlsx'
    elif suffix in {'.csv','.tsv'}:
        rows=_csv_rows(data); raw=rows; source_type='csv'
    elif suffix=='.json':
        rows,raw=_json_rows(data); source_type='json'
    else:
        raise ValueError('Formato no soportado. Usa .xlsx, .csv o .json.')
    if isinstance(rows,list) and len(rows)>MAX_IMPORT_ROWS:
        raise ValueError(f'El archivo contiene más de {MAX_IMPORT_ROWS} casos; divide la suite en archivos más pequeños.')
    normalized=normalize_cases(rows)
    return {
        'filename':filename,'source_type':source_type,'sha256':hashlib.sha256(data).hexdigest(),
        'row_count':len(rows) if isinstance(rows,list) else 0,'cases':normalized,
        'raw_preview':rows[:10] if isinstance(rows,list) else raw,
    }


def _pick(row: dict, aliases: list[str]) -> str:
    idx={_norm_header(str(k)):v for k,v in row.items()}
    for alias in aliases:
        a=_norm_header(alias)
        if a in idx and _cell_text(idx[a]): return _cell_text(idx[a])
    for k,v in idx.items():
        if any(_norm_header(a) in k for a in aliases) and _cell_text(v): return _cell_text(v)
    return ''


def normalize_cases(rows) -> list[dict]:
    if not isinstance(rows,list): rows=[rows]
    out=[]
    for i,row in enumerate(rows,1):
        if not isinstance(row,dict):
            out.append({'case_key':f'IMP-{i:03d}','name':f'Caso importado {i}','description':_cell_text(row),'precondition':'','action':'','expected':'','raw':row})
            continue
        # Internal plan JSON can bypass AI planning later.
        if isinstance(row.get('plan'),dict) and isinstance(row['plan'].get('steps'),list):
            out.append({'case_key':str(row.get('case_key') or row.get('id') or f'IMP-{i:03d}'),'name':str(row.get('name') or row.get('title') or f'Caso {i}'),'description':str(row.get('description') or ''),'precondition':'','action':'','expected':'','raw':row,'prebuilt_plan':row['plan']})
            continue
        key=_pick(row,['id','caso','case id','case_key','codigo','código','identificador']) or f'IMP-{i:03d}'
        name=_pick(row,['titulo del caso de uso','título del caso de uso','titulo','título','nombre','name','caso de uso','test case']) or f'Caso {key}'
        category=_pick(row,['categoria','categoría','category','modulo','módulo'])
        pre=_pick(row,['precondicion','precondición','precondition','given','condicion inicial','condición inicial'])
        action=_pick(row,['accion','acción','action','pasos','steps','cuando','when'])
        expected=_pick(row,['resultado esperado','expected result','expected','entonces','then','criterio de aceptacion','criterio de aceptación'])
        description=_pick(row,['descripcion','descripción','description','detalle','observacion','observación'])
        out.append({'case_key':key,'name':name,'category':category,'description':description,'precondition':pre,'action':action,'expected':expected,'raw':row})
    return out
