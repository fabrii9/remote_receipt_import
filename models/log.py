# -*- coding: utf-8 -*-
import base64
import io

from odoo import api, fields, models

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment
except Exception:
    openpyxl = None


class RemotePaymentImportLog(models.Model):
    _name = "remote.payment.import.log"
    _description = "Log de Importación de Pagos Remotos"
    _order = "create_date desc"

    name = fields.Char(string="Nombre", default=lambda self: self._default_name())
    file_name = fields.Char(string="Archivo origen")
    lines_ids = fields.One2many("remote.payment.import.log.line", "log_id", string="Líneas")

    total_rows = fields.Integer(string="Filas procesadas", compute="_compute_counts", store=False)
    approved_count = fields.Integer(string="Aprobados", compute="_compute_counts", store=False)
    skipped_count = fields.Integer(string="No aprobados", compute="_compute_counts", store=False)

    # Binario para descarga
    export_file = fields.Binary(string="Archivo exportado")
    export_filename = fields.Char(string="Nombre de exportación")

    def _default_name(self):
        return fields.Datetime.now().strftime("Import %Y-%m-%d %H:%M:%S")

    def _compute_counts(self):
        for rec in self:
            rec.total_rows = len(rec.lines_ids)
            rec.approved_count = sum(1 for l in rec.lines_ids if l.status == 'approved')
            rec.skipped_count = rec.total_rows - rec.approved_count

    # -----------------------------
    # Exportar a Excel (.xlsx)
    # -----------------------------
    def action_download_xlsx(self):
        self.ensure_one()
        if not openpyxl:
            # Si faltara la dependencia (muy raro porque ya usás openpyxl en el módulo)
            raise ValueError("Falta la dependencia 'openpyxl' para exportar a Excel.")

        # Mapeo legible del estado
        status_map = dict(
            self.env['remote.payment.import.log.line']._fields['status']._description_selection(self.env)
        )

        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Líneas"

        # Encabezados
        headers = [
            "Fecha de Pago",
            "CUIT/DNI (Tipo de Operación)",
            "Memo (Operación Relacionada)",
            "Importe",
            "Estado",
            "Partner ID (Odoo 18)",
            "Partner Nombre",
            "Deuda detectada",
            "ID Payment (Odoo 18)",
            "Mensaje",
        ]
        ws.append(headers)

        # Estilo encabezados
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")

        # Filas
        for l in self.lines_ids:
            ws.append([
                l.fecha_pago and l.fecha_pago.strftime("%Y-%m-%d") or "",
                l.tipo_operacion or "",
                l.operacion_relacionada or "",
                l.importe or 0.0,
                status_map.get(l.status, l.status or ""),
                l.partner_id or 0,
                l.partner_name or "",
                l.deuda_detectada or 0.0,
                l.payment_id or 0,
                l.message or "",
            ])

        # Auto ancho de columnas
        for col in ws.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                try:
                    val = str(cell.value) if cell.value is not None else ""
                except Exception:
                    val = ""
                max_len = max(max_len, len(val))
            ws.column_dimensions[col_letter].width = min(max(10, max_len + 2), 60)

        # Guardar en binario
        bio = io.BytesIO()
        wb.save(bio)
        data = bio.getvalue()

        fname = (self.name or "log") + ".xlsx"
        # Usamos sudo para evitar errores de permisos de escritura sobre el registro
        self.sudo().write({
            "export_file": base64.b64encode(data),
            "export_filename": fname,
        })

        # Devolver acción de descarga
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content?model=remote.payment.import.log"
                   "&id=%d&field=export_file&filename_field=export_filename&download=true" % self.id,
            "target": "self",
        }


class RemotePaymentImportLogLine(models.Model):
    _name = "remote.payment.import.log.line"
    _description = "Línea de Log de Importación"

    log_id = fields.Many2one("remote.payment.import.log", required=True, ondelete="cascade")

    fecha_pago = fields.Date(string="Fecha de Pago")
    # OJO: estos labels en la vista se renombran, acá quedan como campos "técnicos"
    tipo_operacion = fields.Char(string="Tipo de Operación")  # aquí guardamos CUIT/DNI (archivo)
    operacion_relacionada = fields.Char(string="Operación Relacionada (CUIT/DNI)")  # aquí guardamos MEMO (archivo)
    importe = fields.Float(string="Importe")

    partner_id = fields.Integer(string="Partner ID (Odoo 18)")
    partner_name = fields.Char(string="Partner Nombre")
    deuda_detectada = fields.Float(string="Deuda detectada")
    payment_id = fields.Integer(string="ID Payment (Odoo 18)")

    status = fields.Selection([
        ('approved', 'Aprobado'),
        ('partner_not_found', 'Partner no encontrado'),
        ('mismatch', 'Importe != Deuda'),
        ('error', 'Error'),
    ], string="Estado", default='error')

    message = fields.Text(string="Mensaje")
