# Migración a Enterprise v1

1. Haz una copia de seguridad de `.env` y `data/`.
2. Reemplaza el código por Enterprise v1.
3. Conserva las bases SQLite y claves locales existentes.
4. Ejecuta de nuevo `pip install -r requirements.txt`.
5. Configura al menos una API de IA en `.env`; para failover configura dos o más.
6. Reinicia con `start_windows.bat` o `start_macos.command`.
7. En AI & Learning confirma que la memoria anterior aparece.
8. En SQL Lab prueba `Estado Router` y `Probar proveedores`.
9. En QA Workspace importa un caso pequeño antes de lanzar una suite completa.
