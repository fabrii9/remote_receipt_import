# -*- coding: utf-8 -*-
import base64
import csv
import io

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RemotePaymentImportLog(models.Model):
    _name = "remote.payment.import.log"
    _description = "Log de Importación de Pagos Remotos"
    _order = "create_date desc"

    name = fields.Char(string="Nombre", default=lambda self: self._default_name())
    file_name = fields.Char(string="Archivo origen")

    # OJO: conservamos tu nombre de campo existente
    lines_ids = fields.One2many(
        "remote.payment.import.log.line", "log_id", string="Líneas"
    )

    total_rows = fields.Integer(string="Filas procesadas", compute="_compute_counts", store=False)
    approved_count = fields.Integer(string="Aprobados", compute="_compute_counts", store=False)
    skipped_count = fields.Integer(string="No aprobados", compute="_compute_counts", store=False)

    # Campos para exportación/descarga
    export_file = fields.Binary(string="Archivo exportado", readonly=True)
    export_filename = fields.Char(string="Nombre archivo")

    def _default_name(self):
        return fields.Datetime.now().strftime("Import %Y-%m-%d %H:%M:%S")

    def _compute_counts(self):
        for rec in self:
            rec.total_rows = len(rec.lines_ids)
            rec.approved_count = sum(1 for l in rec.lines_ids if l.status == 'approved')
            rec.skipped_count = rec.total_rows - rec.approved_count

    def action_download_csv(self):
        """Genera un CSV con las líneas del log y lo sirve por /web/content."""
        self.ensure_one()

        header = [
            "Log", "Archivo Origen", "Fecha de Registro",
            "Fecha Pago", "Tipo Operación", "ID (CUIT/DNI)", "Importe",
            "Estado", "Partner (ID remoto)", "Partner (nombre)",
            "Deuda detectada", "Payment (ID remoto)", "Mensaje"
        ]

        # Labels del selection 'status' (desde el modelo de LÍNEAS)
        status_field_info = self.env['remote.payment.import.log.line'].fields_get(['status'])
        status_selection = dict(status_field_info['status']['selection'])

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)

        for l in self.lines_ids:
            fecha_log = fields.Datetime.to_string(self.create_date) if self.create_date else ""
            fecha_pago = l.fecha_pago.strftime("%Y-%m-%d") if getattr(l.fecha_pago, "strftime", None) else (l.fecha_pago or "")
            status_label = status_selection.get(l.status, l.status or "")

            writer.writerow([
                self.name or "",
                self.file_name or "",
                fecha_log,
                fecha_pago,
                l.tipo_operacion or "",
                l.operacion_relacionada or "",
                ("%.2f" % (l.importe or 0.0)),
                status_label,
                l.partner_id or "",
                l.partner_name or "",
                ("%.2f" % (l.deuda_detectada or 0.0)),
                l.payment_id or "",
                (l.message or "").replace("\n", " | ").replace("\r", " "),
            ])

        # BOM para que Excel abra bien en UTF-8
        csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
        buf.close()
        b64 = base64.b64encode(csv_bytes)
        filename = (self.file_name or self.name or "log") + ".csv"

        # Guardamos con sudo() para evitar restricciones de escritura del usuario
        self.sudo().write({
            "export_file": b64,
            "export_filename": filename,
        })

        return {
            "type": "ir.actions.act_url",
            "url": "/web/content?model=remote.payment.import.log&id=%d&field=export_file&download=1&filename=%s"
                % (self.id, filename),
            "target": "self",
        }

class RemotePaymentImportLogLine(models.Model):
    _name = "remote.payment.import.log.line"
    _description = "Línea de Log de Importación"

    log_id = fields.Many2one("remote.payment.import.log", required=True, ondelete="cascade")
    fecha_pago = fields.Date(string="Fecha de Pago")
    tipo_operacion = fields.Char(string="Tipo de Operación")
    operacion_relacionada = fields.Char(string="Operación Relacionada (CUIT/DNI)")
    importe = fields.Float(string="Importe")

    partner_id = fields.Integer(string="Partner ID (Odoo 18)")
    partner_name = fields.Char(string="Partner Nombre")
    deuda_detectada = fields.Float(string="Deuda detectada")
    payment_id = fields.Integer(string="ID Payment (Odoo 18)")

    status = fields.Selection([
        ('approved', 'Aprobado'),
        ('in_process', 'En proceso'),
        ('created', 'Creado (borrador)'),
        ('partner_not_found', 'Partner no encontrado'),
        ('mismatch', 'Importe != Deuda'),
        ('error', 'Error'),
    ], string="Estado", default='error')


    message = fields.Text(string="Mensaje")
