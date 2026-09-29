# Decisiones de diseño

## ¿Por qué private app y no OAuth?

La integración trabaja contra una sola cuenta de HubSpot. OAuth tiene sentido cuando una app se instala en muchas cuentas y hay que manejar access y refresh tokens por cada una. Con una private app basta un token con los permisos mínimos, guardado como secreto.

## ¿Por qué upsert y no create + update?

Con create/update hay que consultar primero si el contacto existe (una llamada extra por lote) y hay condiciones de carrera. El upsert por una propiedad única resuelve ambos casos en una llamada y hace el proceso **idempotente**: correr dos veces el mismo lote deja el mismo resultado.

Limitación documentada por HubSpot: con `email` como `idProperty` no se permiten upserts parciales. Para eso conviene una propiedad de identificador único propia (por ejemplo `external_id` del sistema origen).

## Reintentos: qué sí y qué no

| Respuesta | ¿Reintentar? | Motivo |
|---|---|---|
| 429 | Sí, esperando `Retry-After` | Es temporal: se superó el límite de llamadas |
| 500, 502, 503, 504 | Sí, con backoff exponencial | Fallo temporal del servidor |
| Error de red o timeout | Sí | Fallo temporal |
| 400, 401, 403, 404 | No | El problema está en la petición o en el token; repetirla no lo arregla |

El jitter (unos milisegundos aleatorios) evita que varios procesos reintenten exactamente al mismo tiempo.

## Un lote que falla no detiene el proceso

Si un lote recibe un 400 (por ejemplo, un valor inválido en una propiedad), se registran sus 100 registros como fallidos y el proceso sigue con el siguiente. Al final, el CSV de fallidos permite corregir y reenviar solo esos registros.

## Checkpoint

Tras cada lote completado se guarda su índice junto con una huella (hash) del archivo de entrada. Si el proceso se interrumpe, la siguiente ejecución salta los lotes ya enviados. Si el archivo cambió, la huella no coincide y el checkpoint se ignora. Al terminar una pasada completa, el checkpoint se borra.

## Cuándo no usar Zapier o Make

Para unos pocos eventos al día, un middleware es suficiente. A partir de miles de registros, lógica de transformación no trivial o necesidad de trazabilidad (qué falló, por qué, y reintentarlo), el código propio da más control y sale más barato que pagar por cada tarea ejecutada.
