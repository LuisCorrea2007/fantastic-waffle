# Migración desde SQL AI Workspace

Conserva estos archivos si existen:

```text
data/memory.sqlite3
data/connections.sqlite3
data/.connection_secret.key
```

Cópialos a la carpeta `data/` de Veris QA AI Enterprise antes de iniciar.

Si ya utilizaste Veris QA AI v0.5/v0.5.1 conserva también:

```text
data/veris_qa_ai.db
data/.master.key
```

La memoria SQL y la memoria de agentes son independientes de OpenRouter/Groq/Gemini/Mistral. El archivo `agent_memory.sqlite3` se crea automáticamente al arrancar.

No necesitas Ollama y puedes eliminar las variables `OLLAMA_*` de tu `.env`.
