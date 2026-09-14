# SQL Router Fix v1.0.1

Corrige respuestas de proveedores IA que contienen texto/instrucciones en lugar de SQL.

## Cambio principal

La generación SQL ahora valida la respuesta **dentro del AI Router**. Si el primer proveedor devuelve markdown, explicación, eco del prompt o SQL inválido, se marca ese intento como fallido y se prueba automáticamente el siguiente proveedor configurado.

También se mejoró la extracción de SQL para bloques ` ```sql `, prefijos `SQL:` y respuestas con texto alrededor.

No elimina ni modifica `data/`, conexiones ni memoria persistente.
