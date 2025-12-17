# Remote Receipt Import (Odoo 16 → Odoo 18) v16.0.1.3

**Optimizaciones de rendimiento v1.3:**
- ✅ **Búsqueda batch de partners**: 1 llamada XML-RPC en lugar de N (reducción ~95% de llamadas)
- ✅ **Commits periódicos**: Libera workers cada 50 filas para que otros endpoints funcionen
- ✅ **Creación batch de logs**: Acumula registros y los crea en grupos
- ✅ **Cache de partners**: Elimina búsquedas redundantes

**Funcionalidad:**
Importa archivos XLSX/CSV con pagos y crea recibos automáticamente en Odoo 18 si el importe coincide con la deuda del cliente.

**Configuración:**
Desde **Contabilidad → Importación Remota → Configurar conexión**.
