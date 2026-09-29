# HubSpot Contact Sync

Integración en Python con la API de CRM de HubSpot, construida con criterio de producción, más un módulo custom en HubL para el CMS.

El objetivo es resolver bien "la parte aburrida" de una integración real: autenticación, rate limits, paginación, reintentos, idempotencia, operaciones batch, manejo de errores y logging, de forma que un sync que falla se vea de inmediato y no muera en silencio.

## Qué hace

- **`sync`**: lee contactos de un CSV, los limpia y valida, y los crea o actualiza en HubSpot con *batch upsert* (100 por llamada).
- **`export`**: descarga todos los contactos de HubSpot a un CSV, siguiendo la paginación por cursor.
- **`hubspot-cms/testimonials.module`**: módulo custom de testimonios con grupo repetible, editable por Marketing sin tocar código.

## Decisiones técnicas

| Problema | Solución |
|---|---|
| Autenticación | Private app con permisos mínimos (`crm.objects.contacts.read/write`); token en variable de entorno, nunca en el código |
| Volumen de llamadas | `POST /crm/v3/objects/contacts/batch/upsert` con 100 registros por lote: 50.000 contactos ≈ 500 llamadas |
| Duplicados | Upsert por `email` normalizado + deduplicación dentro del mismo archivo antes de enviar |
| Rate limits (429) | Respeta el encabezado `Retry-After`; si no viene, backoff exponencial con jitter (1 s, 2 s, 4 s…) |
| Errores 5xx y de red | Mismo esquema de reintentos, con un máximo configurable |
| Errores 4xx | No se reintentan (repetirlos no los arregla); el lote se registra como fallido y el proceso continúa |
| Fallos parciales (HTTP 207) | Cada error por registro se guarda con su email y mensaje |
| Proceso interrumpido | Checkpoint del último lote completado; al relanzar reanuda desde ahí. Como el upsert es idempotente, repetir un lote no duplica nada |
| Visibilidad | Resumen JSON por ejecución (creados, actualizados, fallidos, duración), CSV con los registros fallidos y código de salida ≠ 0 si hubo problemas, para que cron o CI puedan alertar |
| Datos sucios | Emails en minúsculas y validados, nombres capitalizados, teléfonos normalizados, idioma mapeado a `hs_language` |

Más detalle en [`docs/decisiones.md`](docs/decisiones.md).

## Estructura

```
src/hubspot_sync/
├── client.py      # cliente HTTP: auth, reintentos, paginación, batch upsert
├── transform.py   # limpieza, validación, mapeo y deduplicación
├── sync.py        # pipeline: lotes, checkpoint, fallidos, resumen
└── cli.py         # comandos sync / export
tests/             # 19 pruebas con la API simulada (sin llamadas reales)
sample_data/       # CSV de ejemplo con casos borde intencionales
hubspot-cms/testimonials.module/   # módulo HubL
```

## Uso

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env          # pega el token de tu private app
hubspot-sync sync sample_data/contacts.csv --dry-run   # valida sin llamar a la API
hubspot-sync sync sample_data/contacts.csv
hubspot-sync export contacts_export.csv
pytest
```

Salida esperada del `--dry-run` con el CSV de ejemplo:

```
Rows read: 8 | rejected: 2 | duplicates merged: 1
Batches: 1 (skipped via checkpoint: 0)
```

Las 2 filas rechazadas (sin email y con email inválido) y el duplicado son intencionales para demostrar la validación. Los detalles quedan en `output/`.

## Módulo de testimonios (HubL)

- Grupo repetible (1 a 12 testimonios) con foto, nombre, cargo y texto.
- Marcado semántico (`figure`, `blockquote`, `figcaption`) y texto escapado.
- Imágenes con dimensiones explícitas y `loading="lazy"` para cuidar los Core Web Vitals.
- Grilla responsive de 2 o 3 columnas elegida por Marketing.

Para subirlo a un portal con la CLI de HubSpot:

```bash
npm install -g @hubspot/cli
hs init
hs upload hubspot-cms/testimonials.module testimonials.module
```

## Posibles mejoras

- Sincronización incremental por fecha de última modificación en el sistema origen.
- Propiedad de identificador único propio (`external_id`) en lugar del email, que además permite upserts parciales.
- Alertas a Slack cuando la tasa de fallidos supere un umbral.
- Ejecución programada en una función serverless o como custom code action.
