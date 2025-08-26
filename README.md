# Remote Receipt Import (Odoo 16 → Odoo 18) v16.0.1.2

**Cambio clave:** Eliminada la herencia de `base.view_res_config_settings` para evitar errores de XMLID. 
Ahora la configuración se hace desde **Contabilidad → Importación Remota → Configurar conexión**.

Resto igual: importar XLSX/CSV y crear pagos en Odoo 18 si Importe == deuda.
