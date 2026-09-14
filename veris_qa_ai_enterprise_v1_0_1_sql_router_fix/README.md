# Veris QA AI — Enterprise v1

Plataforma local de Quality Engineering para coordinar **Web + Mobile + API + PostgreSQL + IA + memoria propia + evidencias + reportes** desde un Control Center web.

> El objetivo de esta versión es convertir el MVP anterior en una base operativa mucho más completa. No significa que cualquier sistema pueda certificarse “sin errores” automáticamente: los resultados dependen del ambiente QA, datos, accesos, selectores, endpoints, Appium/VPN y criterios de aceptación disponibles.

## Arquitectura

La IA **interpreta y planifica**. El runner determinista **ejecuta y decide PASS/FAIL** con assertions y evidencia real.

```text
Excel / JSON / CSV
       ↓
Case Intake
       ↓
Memoria propia + AI Router
       ↓
Plan QA determinista
       ↓
┌─────────┬──────────┬─────────┬────────────┐
│ Web     │ Mobile   │ API     │ PostgreSQL │
│Playwright│ Appium  │ HTTPX   │ READ ONLY  │
└─────────┴──────────┴─────────┴────────────┘
       ↓
Assertions + evidencias
       ↓
PASS / FAIL / BLOCKED
       ↓
Reportes + experiencias + playbooks
```

## 1. AI Router — sin Ollama

No requiere modelo local.

Proveedores incluidos:

- OpenRouter
- Groq
- Google Gemini
- Mistral
- cualquier endpoint OpenAI-compatible adicional

Orden configurable:

```env
AI_PROVIDER_ORDER=openrouter,groq,gemini,mistral
```

El router:

1. omite proveedores sin key;
2. intenta el primero disponible;
3. si hay timeout, rate limit, cuota, error HTTP o respuesta inválida, intenta el siguiente;
4. coloca proveedores problemáticos temporalmente en cooldown;
5. conserva la memoria aunque cambies de proveedor/modelo.

Las keys permanecen fuera de la UI, en `.env`.

## 2. Memoria propia

La memoria no pertenece a OpenRouter/Groq/Gemini/Mistral.

Archivos principales:

```text
data/memory.sqlite3
data/agent_memory.sqlite3
```

Conserva:

- reglas confirmadas;
- SQL verificado;
- SQL fallido y errores;
- snapshots de esquema PostgreSQL;
- fuentes de casos importados;
- llamadas de agentes;
- playbooks;
- experiencias PASS/FAIL/BLOCKED;
- patrones reutilizables.

Cambiar una API de IA no borra este conocimiento.

## 3. Case Intake — Excel / JSON / CSV / TSV

Desde **QA Workspace** puedes adjuntar:

- `.xlsx`
- `.json`
- `.csv`
- `.tsv`

El importador normaliza columnas como:

- ID / caso / código;
- nombre / título;
- categoría;
- precondición;
- acción / pasos;
- resultado esperado;
- descripción.

Después el AI Router combina:

- caso importado;
- perfiles reales seleccionados;
- memoria QA;
- reglas confirmadas;

y genera un plan ejecutable.

Si todas las APIs de IA fallan, se genera un **borrador heurístico seguro** marcado como `review_required=true` en vez de perder el archivo.

## 4. QA Workspace Web

Playwright gestionado desde el Control Center:

- Chromium;
- Firefox;
- WebKit;
- navegación;
- click;
- fill;
- press;
- select;
- check/uncheck;
- hover;
- assertions visibles/ocultas/texto/URL;
- screenshots;
- consola;
- requests fallidos;
- clic manual sobre la captura integrada;
- agente Web que observa el DOM real y decide el siguiente paso.

El agente Web no puede declarar PASS por sí solo. Para terminar con PASS debe ejecutar una assertion determinista en el navegador real.

## 5. QA Mobile

Integración Appium mediante protocolo W3C HTTP.

### Android

- Appium;
- UiAutomator2;
- APK/path opcional;
- `appPackage`;
- `appActivity`;
- UDID/device;
- locators por accessibility id, id, XPath, texto y UiAutomator.

### iOS

- Appium;
- XCUITest;
- macOS/Xcode requerido;
- bundleId;
- UDID/device/simulator;
- XPath/accessibility id/iOS predicate.

Desde QA Workspace existe visor de screenshot y acciones manuales. Los casos importados también pueden producir `mobile.agent` y pasos Appium deterministas.

## 6. PostgreSQL / SQL Lab

- conexiones cifradas;
- pool persistente;
- READ ONLY a nivel de transacción;
- statement timeout;
- límite de filas;
- streaming;
- caché/snapshot de esquema;
- introspección masiva de columnas, PK y FK;
- SQL manual;
- lenguaje natural → SQL con failover IA;
- consultas confirmadas reutilizables;
- reglas de negocio;
- `db.ai_query` durante un caso QA.

La IA recibe el **esquema**, no necesita recibir los resultados de pacientes para generar SQL.

SQL destructivo permanece bloqueado por defecto.

## 7. API Lab

- perfiles API cifrados;
- GET/POST/PUT/PATCH/DELETE;
- headers;
- Bearer/token;
- query params;
- JSON body;
- tiempo;
- status code;
- headers sensibles redactados;
- assertions dentro del runner.

El planner no debe inventar endpoints: `api.request` se usa cuando el caso/memoria provee un endpoint real.

## 8. Motor de casos

Tipos principales soportados:

### DB

- `db.query`
- `db.ai_query`

### API

- `api.request`

### Web

- `web.start`
- `web.goto`
- `web.click`
- `web.fill`
- `web.press`
- `web.select`
- `web.check`
- `web.uncheck`
- `web.hover`
- `web.expect_visible`
- `web.expect_hidden`
- `web.expect_text`
- `web.expect_page_text`
- `web.expect_url_contains`
- `web.screenshot`
- `web.agent`
- `web.stop`

### Mobile

- `mobile.start`
- `mobile.click`
- `mobile.fill`
- `mobile.back`
- `mobile.expect_visible`
- `mobile.screenshot`
- `mobile.agent`
- `mobile.stop`

### General

- `wait`
- `assert.equals`

## 9. Seguridad

- PBKDF2 para usuario administrador;
- cookies HttpOnly;
- passwords PostgreSQL/Web y tokens API cifrados localmente;
- AI keys solo en `.env`;
- SQL destructivo bloqueado;
- DB READ ONLY;
- límites de tiempo/filas;
- auditoría append-only en `data/audit.jsonl`;
- redacción de secretos/PII antes de enviar contexto a IA externa;
- redacción de PII por defecto en reportes persistentes (`REPORT_REDACT_PII=true`).

Las assertions se evalúan sobre los datos reales **antes** de redactar el reporte.

Para Veris se recomienda trabajar únicamente con usuarios/pacientes QA y ambientes autorizados.

## 10. Instalación Windows

1. Descomprime el proyecto.
2. Ejecuta:

```text
INSTALL_WINDOWS.bat
```

Esto crea `.venv`, instala dependencias y los navegadores Playwright.

3. Edita `.env` y agrega las APIs que quieras utilizar.
4. Ejecuta:

```text
start_windows.bat
```

5. Abre:

```text
http://127.0.0.1:8000
```

### Mobile Android opcional

Instala Node.js LTS y después ejecuta:

```text
INSTALL_MOBILE_WINDOWS.bat
```

Luego inicia Appium en otra terminal:

```powershell
appium
```

## 11. Instalación macOS

```bash
chmod +x setup_macos.command start_macos.command INSTALL_MOBILE_MAC.command
./setup_macos.command
./start_macos.command
```

Para Appium:

```bash
./INSTALL_MOBILE_MAC.command
appium
```

XCUITest requiere Xcode/macOS.

## 12. Configuración IA

Copia/edita `.env` (los instaladores crean uno desde `.env.example` si falta):

```env
AI_PROVIDER_ORDER=openrouter,groq,gemini,mistral

OPENROUTER_API_KEY=
OPENROUTER_MODEL=openrouter/free

GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-20b

GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash

MISTRAL_API_KEY=
MISTRAL_MODEL=mistral-small-latest
```

No necesitas tener las cuatro. Con dos o más configuradas obtienes failover entre proveedores.

Comprueba desde la interfaz:

**SQL Lab → Estado Router → Probar proveedores**

## 13. Primer flujo recomendado

1. Crear ambiente `QA`.
2. Guardar PostgreSQL.
3. Guardar perfil Web.
4. Guardar API si el caso la usa.
5. Guardar Mobile/Appium si el caso es App.
6. En **QA Workspace**, seleccionar los perfiles.
7. Adjuntar Excel/JSON de casos.
8. Pulsar **Interpretar y preparar QA**.
9. Revisar casos marcados `REVISAR`.
10. Ejecutar.
11. Abrir reporte HTML/JSON.
12. Revisar experiencia/playbook guardado en **AI & Learning**.

## 14. Persistencia al actualizar

Antes de reemplazar una versión conserva como mínimo:

```text
.env
data/
```

Especialmente:

```text
data/memory.sqlite3
data/agent_memory.sqlite3
data/veris_qa_ai.db
data/connections.sqlite3
data/.master.key
data/.connection_secret.key
```

No compartas estas claves ni la carpeta `data/` públicamente.

## 15. Qué sigue siendo dependiente de infraestructura real

El código habilita los motores, pero para certificar flujos reales aún necesitas los datos reales del ambiente autorizado:

- URLs QA/UAT;
- VPN/proxy/certificados;
- usuarios QA;
- esquema/tablas;
- endpoints/OpenAPI/Postman;
- APK/bundleId/appPackage;
- dispositivos/emuladores;
- criterios de aceptación;
- selectores o DOM accesible;
- casos funcionales reales.

Ninguna IA puede atravesar por sí sola una VPN/firewall ni sustituir permisos corporativos.
