# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

class RemoteReceiptSettings(models.Model):
    _name = "remote.receipt.settings"
    _description = "Configuración conexión Odoo 18 (Remote Receipt Import)"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = "remote_o18_url"

    remote_o18_url = fields.Char(string="URL Odoo 18 (XML-RPC)", required=True, tracking=True)
    remote_o18_db = fields.Char(string="BD Odoo 18", required=True, tracking=True)
    remote_o18_user = fields.Char(string="Usuario Odoo 18", required=True, tracking=True)
    remote_o18_password = fields.Char(string="Password Odoo 18", required=True)
    remote_payment_journal_id = fields.Integer(string="ID Diario de Cobros (O18)", required=True, tracking=True)
    remote_payment_method_line_id = fields.Integer(string="ID Método de Pago (O18, opcional)", tracking=True)
    amount_tolerance = fields.Float(string="Tolerancia", default=0.01, required=True, tracking=True)
    active = fields.Boolean(string="Activo", default=True, tracking=True)
    
    @api.model
    def get_active_settings(self):
        """Obtiene la configuración activa"""
        settings = self.search([('active', '=', True)], limit=1)
        if not settings:
            raise UserError(_("No hay configuración activa. Por favor configure la conexión primero."))
        return settings
    
    def action_save(self):
        """Guarda también en ir.config_parameter para compatibilidad"""
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param("remote_receipt_import.remote_o18_url", self.remote_o18_url or "")
        ICP.set_param("remote_receipt_import.remote_o18_db", self.remote_o18_db or "")
        ICP.set_param("remote_receipt_import.remote_o18_user", self.remote_o18_user or "")
        ICP.set_param("remote_receipt_import.remote_o18_password", self.remote_o18_password or "")
        ICP.set_param("remote_receipt_import.remote_payment_journal_id", str(self.remote_payment_journal_id or 0))
        ICP.set_param("remote_receipt_import.remote_payment_method_line_id", str(self.remote_payment_method_line_id or 0))
        ICP.set_param("remote_receipt_import.amount_tolerance", str(self.amount_tolerance if self.amount_tolerance is not None else 0.01))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Guardado'),
                'message': _('Configuración guardada correctamente'),
                'type': 'success',
                'sticky': False,
            }
        }

