# Validación de la entrega Enterprise v1

Pruebas ejecutadas sobre el paquete antes de comprimirlo:

- Compilación de todos los módulos Python (`compileall`).
- Validación sintáctica de `static/app.js` con Node (`node --check`).
- Comprobación de IDs HTML únicos y de que las referencias `$('<id>')` del frontend existan.
- AI Router con servidores HTTP simulados: proveedor 1 responde 429, proveedor 2 responde JSON correcto; se verificó failover y cooldown.
- Importador `.xlsx`: se leyó `examples/casos_qa_template.xlsx` sin Excel/Office.
- Planner sin proveedor IA: se verificó fallback heurístico seguro y generación de pasos Mobile + DB.
- MobileManager contra servidor Appium W3C simulado: creación de sesión, click, fill, source y cierre.
- Migración SQLite de `mobile_profiles`: agrega `username/password_enc` a una base de versión anterior.
- Almacenamiento de credenciales Mobile: password cifrado y no expuesto en listados.
- FastAPI con almacenamiento temporal: setup admin, ambiente, perfil Mobile, dashboard e importación/autoguardado de casos XLSX.
- Runner Mobile con manager simulado: ejecución PASS y generación de reporte.

## No certificado desde este entorno

No se pudo certificar contra infraestructura privada de Veris porque requiere acceso real a:

- VPN/red corporativa;
- PostgreSQL real;
- URLs QA/UAT;
- APIs reales;
- usuarios QA;
- dispositivos/emuladores/Appium reales;
- APK/build iOS;
- criterios de aceptación y casos reales.

Por lo tanto esta validación demuestra consistencia del paquete y de los flujos internos, no certificación de producción ni garantía de ausencia total de defectos.
