# Enterprise Setup — Veris QA AI

## 1. Migración desde v0.5/v0.5.1

Copia de la instalación anterior:

- `.env` (después elimina las variables Ollama y agrega keys del AI Router)
- `data/memory.sqlite3`
- `data/connections.sqlite3`
- `data/.connection_secret.key`
- `data/veris_qa_ai.db` si quieres conservar ambientes/perfiles/casos/ejecuciones
- `data/.master.key` si conservas `veris_qa_ai.db`

No copies `__pycache__` ni `.venv` entre versiones de Python distintas.

## 2. AI Router

Configura al menos una API. Para failover real configura dos o más.

```env
AI_PROVIDER_ORDER=openrouter,groq,gemini,mistral
OPENROUTER_API_KEY=
GROQ_API_KEY=
GEMINI_API_KEY=
MISTRAL_API_KEY=
```

El primer proveedor que responda correctamente se utiliza. 401/403/429 activan cooldown inmediato para evitar insistir sobre una key sin autorización/cuota.

## 3. Case Intake

El Excel ideal contiene una fila por caso y encabezados semejantes a:

```text
ID | Categoría | Título del Caso de Uso | Precondición | Acción | Resultado Esperado
```

No es obligatorio que los nombres sean exactamente esos; el importador contempla alias comunes.

## 4. Mobile

El backend no necesita el cliente Python de Appium: habla con el servidor Appium usando W3C WebDriver.

Android:

```text
INSTALL_MOBILE_WINDOWS.bat
appium
```

macOS Android/iOS:

```bash
./INSTALL_MOBILE_MAC.command
appium
```

## 5. Seguridad de datos

No adjuntes archivos con PII clínica real a proveedores externos. Usa datos QA/sintéticos. La aplicación aplica redacción, pero esto no reemplaza la política corporativa ni la revisión de cumplimiento.
