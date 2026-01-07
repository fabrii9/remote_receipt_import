# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PaymentImportCheckpoint(models.Model):
    _name = "payment.import.checkpoint"
    _description = "Checkpoint de procesamiento para recuperación"

    batch_id = fields.Many2one(
        "remote.payment.import.log",
        string="Lote de Importación",
        required=True,
        ondelete="cascade",
        index=True
    )
    
    total_rows = fields.Integer(string="Total de Filas", required=True)
    processed_rows = fields.Integer(string="Filas Procesadas", default=0)
    last_processed_id = fields.Integer(string="Último ID Procesado")
    
    state = fields.Selection([
        ('running', 'En Ejecución'),
        ('paused', 'Pausado'),
        ('completed', 'Completado'),
        ('failed', 'Fallido'),
    ], string="Estado", default='running', required=True)
    
    started_at = fields.Datetime(string="Iniciado", required=True, default=fields.Datetime.now)
    last_checkpoint_at = fields.Datetime(string="Último Checkpoint", default=fields.Datetime.now)
    completed_at = fields.Datetime(string="Completado")
    
    success_count = fields.Integer(string="Exitosos", default=0)
    failed_count = fields.Integer(string="Fallidos", default=0)
    skipped_count = fields.Integer(string="Omitidos", default=0)
    
    error_message = fields.Text(string="Mensaje de Error")
    
    # Progreso
    progress_percentage = fields.Float(
        string="Progreso (%)",
        compute="_compute_progress",
        store=True
    )
    
    @api.depends('processed_rows', 'total_rows')
    def _compute_progress(self):
        for record in self:
            if record.total_rows > 0:
                record.progress_percentage = (record.processed_rows / record.total_rows) * 100
            else:
                record.progress_percentage = 0.0

    def update_progress(self, processed_count=1, success=False, failed=False, skipped=False):
        """Actualiza el progreso del checkpoint."""
        vals = {
            'processed_rows': self.processed_rows + processed_count,
            'last_checkpoint_at': fields.Datetime.now(),
        }
        if success:
            vals['success_count'] = self.success_count + 1
        if failed:
            vals['failed_count'] = self.failed_count + 1
        if skipped:
            vals['skipped_count'] = self.skipped_count + 1
        
        self.write(vals)

    def mark_completed(self):
        """Marca el checkpoint como completado."""
        self.write({
            'state': 'completed',
            'completed_at': fields.Datetime.now(),
        })

    def mark_failed(self, error_msg):
        """Marca el checkpoint como fallido."""
        self.write({
            'state': 'failed',
            'error_message': error_msg,
        })
