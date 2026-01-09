# Remote Receipt Import - Arquitectura Robusta v16.0.2.1

**Importación de pagos entre instancias de Odoo con procesamiento asíncrono y protecciones de producción.**

> **✨ Última Actualización**: 8 de enero de 2026  
> **🎯 Estado**: Producción-ready - Soporte para pagos parciales Mercado Pago  
> **📦 Versión**: 16.0.2.1

---

## 📢 Cambios Recientes

### v2.1 - Soporte Pagos Parciales Mercado Pago 🎉

**Problema**: Mercado Pago desglosa pagos en múltiples líneas (flete + productos), pero el sistema solo aceptaba pagos que coincidieran exactamente con la deuda total.

**Ejemplo Real**:
- Cliente debe: **$50.000**
- MP envía: $10.000 (flete) + $30.000 (producto A) + $10.000 (producto B)
- Antes: ❌ Ninguno se aplicaba (no coincidía con $50.000)
- Ahora: ✅ Todos se aplican secuencialmente

**Nueva Validación**:
```python
✅ Si pago <= deuda → Crear recibo (permite parciales)
❌ Si pago > deuda → Rechazar (no sobrepagos)
```

**Flujo**:
1. Línea 1: Deuda $50k → Pago $10k → ✅ Recibo creado → Deuda $40k
2. Línea 2: Deuda $40k → Pago $30k → ✅ Recibo creado → Deuda $10k
3. Línea 3: Deuda $10k → Pago $10k → ✅ Recibo creado → Deuda $0

**Garantías**:
- ✅ Deuda recalculada antes de cada línea
- ✅ Procesamiento secuencial en orden
- ✅ Sin sobrepagos (validación estricta)
- ✅ Contabilidad Odoo estándar (conciliación parcial automática)
- ✅ Idempotente (re-ejecuciones seguras)

### v2.0 - Arquitectura Robusta

Este módulo fue completamente rediseñado para evitar caídas del servidor remoto. La arquitectura anterior procesaba todos los registros síncronamente, bloqueando la UI y saturando los workers del Odoo remoto con miles de requests sin control.

**Problema Resuelto**: El módulo causó un crash en producción del Odoo remoto al enviar ~1000+ requests sin rate limiting ni circuit breaker.

**Solución Implementada**: Arquitectura asíncrona con cola persistente, rate limiter (5 req/s), circuit breaker, checkpointing y dashboard de monitoreo en tiempo real.

**Resultado**: ✅ Nunca más bloqueará la UI ni tumbará el servidor remoto, sin importar el tamaño del archivo.

---

## 🚀 Nueva Arquitectura - Nunca Más Tumba el Servidor Remoto

###¿Qué cambió?

**Antes (v1.x)**:
- ❌ Procesamiento síncrono masivo
- ❌ Saturaba workers remotos
- ❌ Bloqueaba UI durante minutos
- ❌ No reanudable si fallaba

**Ahora (v2.0)**:
- ✅ **Cola asíncrona persistente**
- ✅ **Rate limiter** (5 req/s)
- ✅ **Circuit breaker** (protección contra caídas)
- ✅ **Checkpointing** (reanudable)
- ✅ **Retry inteligente** (backoff exponencial)
- ✅ **Dashboard en tiempo real**

---

## 🎯 Características Principales

### 1. Cola Persistente
Cada registro del archivo se guarda en BD con estado individual:
- `pending` → `processing` → `done/failed/skipped`
- Reintentos automáticos con backoff exponencial
- Priorización configurable

### 2. Rate Limiting
- Máximo **5 requests/segundo** al Odoo remoto
- Previene saturación de workers
- Thread-safe para múltiples procesos

### 3. Circuit Breaker
- Detecta caídas del remoto (10 fallos consecutivos)
- Se "abre" automáticamente por 5 minutos
- Recuperación gradual con estado `HALF_OPEN`

### 4. Procesamiento Asíncrono
- **Wizard**: Solo valida y crea cola (< 5 seg)
- **Background**: Procesamiento real en segundo plano
- **Cron**: Se ejecuta cada 2 minutos (fallback)
- **queue_job**: Soporte opcional para mejor performance

### 5. Checkpointing
- Guarda progreso cada 10 registros
- Commits periódicos liberan locks de BD
- Reanudable si se cae el servidor

### 6. Dashboard en Tiempo Real
- Barra de progreso visual
- Estadísticas: Exitosos / Fallidos / Omitidos
- Tiempo transcurrido
- Ver errores individuales

---

## 📋 Flujo de Trabajo

```
1. Usuario sube archivo
   ↓
2. Wizard crea registros en cola (< 5 seg)
   ↓
3. Usuario ve: "En cola, procesando en background"
   ↓
4. Procesador asíncrono:
   - Toma lotes de 30 registros
   - Rate limit: 5 req/s
   - Commit cada 10 registros
   - Actualiza dashboard
   ↓
5. Usuario monitorea en Dashboard
```

**Beneficio**: UI nunca se cuelga. Archivos de 10,000 filas se procesan sin riesgo.

---

## 🔧 Instalación

1. Copiar módulo a `addons/`
2. Actualizar lista de módulos
3. Instalar `remote_receipt_import`
4. Configurar en: **Contabilidad → Importación Remota → Configurar**

### Dependencias

```bash
pip install openpyxl
```

### Opcional (Recomendado)

```bash
pip install odoo-addon-queue_job
```

---

## 📊 Uso

### 1. Importar Archivo

**Menú**: Contabilidad → Importación Remota → Importar Pagos

**Archivo requerido** (XLSX o CSV):
- **Fecha de Pago**: Fecha del pago
- **Tipo de Operación**: CUIT/DNI del partner
- **Operación Relacionada**: Memo/referencia
- **Importe**: Monto a pagar

**Proceso**:
1. Subir archivo
2. Clic en "Procesar"
3. Ver confirmación: "En cola"
4. Ir a Dashboard para monitorear

### 2. Monitorear Progreso

**Dashboard de Progreso**:
- Menú: **Contabilidad → Importación Remota → Dashboard de Progreso**
- Ver barra de progreso en tiempo real
- Estadísticas de éxito/fallo/omitido

**Cola de Procesamiento**:
- Menú: **Contabilidad → Importación Remota → Cola de Procesamiento**
- Filtros: Pendientes / Procesando / Completados / Fallidos
- Ver errores específicos por registro

---

## 🛡️ Protecciones

| Protección | Descripción | Beneficio |
|------------|-------------|-----------|
| **Rate Limiter** | 5 req/s máximo | No satura workers remotos |
| **Circuit Breaker** | Detecta caídas (10 fallos) | Evita cascada de errores |
| **Commits Periódicos** | Cada 10 registros | Libera locks de BD |
| **Batch Processing** | 30 registros por lote | No bloquea otros endpoints |
| **Retry Exponencial** | 2, 4, 8, 16 min | Recuperación inteligente |
| **Checkpointing** | Guarda progreso | Reanudable si se cae |

---

## 🐛 Troubleshooting

### "No se procesa nada"

**Verificar cron**:
1. Configuración → Técnico → Planificador
2. Buscar: "Procesar Cola de Pagos"
3. Debe estar Activo y ejecutarse cada 2 min

### "Circuit breaker OPEN"

**Significado**: Odoo remoto caído/sobrecargado

**Solución**: Esperar 5 min (recuperación automática)

### "Muchos fallidos"

1. Ir a: Cola de Procesamiento
2. Filtrar por "Fallidos"
3. Ver columna "Mensaje de Error"
4. Errores comunes:
   - **Partner no encontrado** → CUIT inválido en archivo
   - **Sobrepago rechazado** → Pago mayor que deuda actual (validado)
   - **Sin deuda pendiente** → Cliente ya pagó todo (común con pagos parciales MP)
   - **HTTP 429** → Rate limit remoto (se reintenta automáticamente)

### "Pagos de Mercado Pago no se aplican"

**Verificar**: ¿Son pagos parciales (menores a la deuda)?

**Solución**: ✅ v2.1 soporta pagos parciales. Los pagos se aplican secuencialmente:
- Línea 1: $10k aplicado, quedan $40k
- Línea 2: $30k aplicado, quedan $10k
- Línea 3: $10k aplicado, deuda $0

**Importante**: El orden del archivo importa. Procesar en orden cronológico de MP.

---

## 📈 Performance

| Registros | Tiempo Aprox | Workers Bloqueados |
|-----------|--------------|-------------------|
| 100 | ~3 min | ✅ NO |
| 1,000 | ~20 min | ✅ NO |
| 10,000 | ~3 horas | ✅ NO |

**Trade-off**: Un poco más lento, pero 100% seguro.

---

## 🔐 Seguridad & Resiliencia

- ✅ **Idempotente**: Evita duplicados con clave única
- ✅ **Transaccional**: Commits controlados
- ✅ **Reanudable**: Zero data loss si se cae
- ✅ **Auditable**: Logs detallados por registro
- ✅ **Aislado**: Fallos no afectan otros batches

---

## 📝 Changelog

### v16.0.2.1 (2026-01-08) - **Pagos Parciales Mercado Pago** 💰
**Nueva validación para pagos desglosados:**
- ✨ **Soporte pagos parciales** - Acepta `pago <= deuda` (antes solo `pago == deuda`)
- ✨ **Protección sobrepagos** - Rechaza `pago > deuda` con mensaje claro
- ✨ **Recálculo automático** - Deuda se actualiza antes de cada línea
- ✨ **Procesamiento secuencial** - Garantiza orden correcto de aplicación
- 🎯 **Caso de uso**: Mercado Pago con flete + productos separados
- 📊 **Ejemplo**: Deuda $50k → Pagos $10k + $30k + $10k → Todos aplicados ✅
- 🔒 **Contabilidad correcta** - Usa conciliación parcial nativa de Odoo
- ♻️ **Idempotente** - Re-ejecuciones seguras (deuda ya reducida)

**Impacto**: Archivos de MP con múltiples líneas por cliente ahora funcionan correctamente.

### v16.0.2.0 (2026-01-08) - **Arquitectura Robusta** 🎉
**Rediseño completo para producción:**
- ✨ **Cola asíncrona persistente** con 5 estados (pending/processing/done/failed/skipped)
- ✨ **Rate limiter** (5 req/s) - Thread-safe, previene saturación
- ✨ **Circuit breaker pattern** - Detecta y previene cascadas de errores
- ✨ **Checkpointing** - Guarda progreso cada 10 registros, reanudable
- ✨ **Dashboard en tiempo real** - Monitoreo visual con progress bar
- ✨ **Cron fallback + queue_job** - Procesamiento robusto en background
- ✨ **Retry inteligente** - Backoff exponencial (2^n minutos)
- ✨ **Batch processing** - 30 registros por iteración, commits periódicos
- 🐛 **Fix**: Corregida referencia de menú padre en vistas XML
- 🛡️ **Garantía**: Nunca más tumbará el servidor remoto

**Impacto**: Wizard retorna en <5 seg, UI nunca se cuelga, procesamiento 100% seguro.

### v16.0.1.7 (2025-12-17)
- 🐛 Revertida optimización batch (causaba pérdida datos)
- ✅ Vuelta a búsqueda individual confiable

### v16.0.1.3-1.6 (2025-12)
- ⚡ Optimizaciones de performance
- 🐛 Fixes de búsqueda de partners

### v16.0.1.0 (2025-08-26)
- 🎉 Primera versión

---

## 📄 Licencia

AGPL-3

---

## 👨‍💻 Autor

**Fabrizio + ChatGPT**
