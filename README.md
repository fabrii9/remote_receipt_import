# Remote Receipt Import (Odoo 16 → Odoo 18) v16.0.1.6

**Fix crítico v1.6:**
- 🔧 **Búsqueda exacta de CUITs**: Ahora busca partners usando el valor EXACTO del Excel (sin normalizar)
- ✅ **Sin pérdida de datos**: No se pierden partners por normalización excesiva
- 📋 **Múltiples formatos**: Soporta CUITs con/sin guiones, DNIs con/sin puntos, y valores sin formato

**Mejoras UX v1.4:**
- 🎯 **Barra de progreso visual**: Muestra el estado del proceso en tiempo real
- 📊 **Notificaciones**: Alertas sobre inicio y finalización de la importación
- ✅ **Feedback continuo**: El usuario ve cuántas filas se han procesado
- 🔄 **Proceso no bloqueante**: Puedes cerrar el wizard mientras continúa el procesamiento
- 📈 **Resumen de resultados**: Muestra estadísticas al finalizar

**Optimizaciones de rendimiento v1.3:**
- ✅ **Búsqueda batch de partners**: 1 llamada XML-RPC en lugar de N (reducción ~95% de llamadas)
- ✅ **Commits periódicos**: Libera workers cada 50 filas para que otros endpoints funcionen
- ✅ **Creación batch de logs**: Acumula registros y los crea en grupos
- ✅ **Cache de partners**: Elimina búsquedas redundantes

**Funcionalidad:**
Importa archivos XLSX/CSV con pagos y crea recibos automáticamente en Odoo 18 si el importe coincide con la deuda del cliente.

**Configuración:**
Desde **Contabilidad → Importación Remota → Configurar conexión**.
