# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

class RemoteReceiptSettingsWizard(models.TransientModel):
    _name = "remote.receipt.settings.wizard"
    _description = "Configurar conexión Odoo 18 (Remote Receipt Import)"

    remote_o18_url = fields.Char(string="URL Odoo 18 (XML-RPC)")
    remote_o18_db = fields.Char(string="BD Odoo 18")
    remote_o18_user = fields.Char(string="Usuario Odoo 18")
    remote_o18_password = fields.Char(string="Password Odoo 18")
    remote_payment_journal_id = fields.Integer(string="ID Diario de Cobros (O18)")
    remote_payment_method_line_id = fields.Integer(string="ID Método de Pago (O18, opcional)")
    amount_tolerance = fields.Float(string="Tolerancia", default=0.01)

    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ICP = self.env['ir.config_parameter'].sudo()
        res.update({
            "remote_o18_url": ICP.get_param("remote_receipt_import.remote_o18_url", ""),
            "remote_o18_db": ICP.get_param("remote_receipt_import.remote_o18_db", ""),
            "remote_o18_user": ICP.get_param("remote_receipt_import.remote_o18_user", ""),
            "remote_o18_password": ICP.get_param("remote_receipt_import.remote_o18_password", ""),
            "remote_payment_journal_id": int(ICP.get_param("remote_receipt_import.remote_payment_journal_id") or 0),
            "remote_payment_method_line_id": int(ICP.get_param("remote_receipt_import.remote_payment_method_line_id") or 0),
            "amount_tolerance": float(ICP.get_param("remote_receipt_import.amount_tolerance") or 0.01),
        })
        return res

    def action_save(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param("remote_receipt_import.remote_o18_url", self.remote_o18_url or "")
        ICP.set_param("remote_receipt_import.remote_o18_db", self.remote_o18_db or "")
        ICP.set_param("remote_receipt_import.remote_o18_user", self.remote_o18_user or "")
        ICP.set_param("remote_receipt_import.remote_o18_password", self.remote_o18_password or "")
        ICP.set_param("remote_receipt_import.remote_payment_journal_id", str(self.remote_payment_journal_id or 0))
        ICP.set_param("remote_receipt_import.remote_payment_method_line_id", str(self.remote_payment_method_line_id or 0))
        ICP.set_param("remote_receipt_import.amount_tolerance", str(self.amount_tolerance if self.amount_tolerance is not None else 0.01))
        return {"type": "ir.actions.act_window_close"}
