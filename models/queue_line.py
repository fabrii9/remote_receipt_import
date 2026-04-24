# -*- coding: utf-8 -*-
from datetime import timedelta
from odoo import api, fields, models


class PaymentImportQueueLine(models.Model):
    _name = "payment.import.queue.line"
    _description = "Cola de procesamiento de pagos"
    _order = "priority desc, id asc"

    # Relación con el log/batch
    batch_id = fields.Many2one(
        "remote.payment.import.log",
        string="Lote de Importación",
        required=True,
        ondelete="cascade",
        index=True
    )
    
    # Datos del pago (almacenados como JSON para flexibilidad)
    row_number = fields.Integer(string="Número de Fila", required=True)
    fecha_pago = fields.Date(string="Fecha de Pago")
    tipo_operacion = fields.Char(string="CUIT/DNI")
    operacion_relacionada = fields.Char(string="Operación Relacionada")
    importe = fields.Float(string="Importe")
    row_data = fields.Text(string="Datos JSON", help="Datos completos de la fila en formato JSON")
    
    # Control de estado
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('processing', 'Procesando'),
        ('done', 'Completado'),
        ('failed', 'Fallido'),
        ('skipped', 'Omitido'),
    ], string="Estado", default='pending', required=True, index=True)
    
    # Control de reintentos
    attempts = fields.Integer(string="Intentos", default=0)
    max_attempts = fields.Integer(string="Máx. Intentos", default=5)
    scheduled_date = fields.Datetime(
        string="Próximo Intento",
        help="Fecha programada para el próximo intento de procesamiento"
    )
    
    # Resultados del procesamiento
    partner_id = fields.Integer(string="Partner ID (Remoto)")
    partner_name = fields.Char(string="Nombre del Partner")
    payment_id = fields.Integer(string="Payment ID (Remoto)")
    error_message = fields.Text(string="Mensaje de Error")
    
    # Prioridad
    priority = fields.Integer(string="Prioridad", default=10, help="Menor número = mayor prioridad")
    
    # Metadatos
    processing_time = fields.Float(string="Tiempo de Procesamiento (s)")
    create_date = fields.Datetime(string="Fecha de Creación", readonly=True)
    write_date = fields.Datetime(string="Última Actualización", readonly=True)

    def mark_as_processing(self):
        """Marca el registro como en procesamiento."""
        self.write({
            'state': 'processing',
            'attempts': self.attempts + 1,
        })

    def mark_as_done(self, partner_id=None, partner_name=None, payment_id=None):
        """Marca el registro como completado exitosamente."""
        vals = {'state': 'done'}
        if partner_id:
            vals['partner_id'] = partner_id
        if partner_name:
            vals['partner_name'] = partner_name
        if payment_id:
            vals['payment_id'] = payment_id
        self.write(vals)

    def mark_as_failed(self, error_msg):
        """Marca el registro como fallido.

        Si el error es un 429 Too Many Requests, siempre reprograma sin importar
        la cantidad de intentos — nunca se marca permanentemente como fallido.
        """
        is_rate_limit = "429" in str(error_msg) or "Too Many Requests" in str(error_msg)
        if not is_rate_limit and self.attempts >= self.max_attempts:
            self.write({
                'state': 'failed',
                'error_message': error_msg,
            })
        else:
            # Backoff exponencial: 2^attempts minutos (mín. 1, máx. 60)
            backoff_minutes = min(60, max(1, 2 ** self.attempts))
            scheduled_date = fields.Datetime.now() + timedelta(minutes=backoff_minutes)
            self.write({
                'state': 'pending',
                'error_message': error_msg,
                'scheduled_date': scheduled_date,
            })

    def mark_as_skipped(self, reason):
        """Marca el registro como omitido (ej: sin CUIT válido)."""
        self.write({
            'state': 'skipped',
            'error_message': reason,
        })

    @api.model
    def cron_process_all_batches(self):
        """
        Método llamado por cron para procesar batches pendientes.
        Se ejecuta cada X minutos (configurado en cron).
        """
        import logging
        _logger = logging.getLogger(__name__)
        
        # Buscar checkpoints en estado 'running'
        checkpoints = self.env["payment.import.checkpoint"].sudo().search([
            ('state', '=', 'running')
        ], order='started_at asc', limit=5)  # Máximo 5 batches concurrentes
        
        if not checkpoints:
            return
        
        _logger.info(f"🔄 Cron: procesando {len(checkpoints)} batches activos")
        
        for checkpoint in checkpoints:
            try:
                # Procesar batch
                self.process_queue_batch(
                    batch_id=checkpoint.batch_id.id,
                    checkpoint_id=checkpoint.id,
                    batch_size=30
                )
            except Exception as e:
                _logger.error(f"❌ Error en cron procesando batch {checkpoint.batch_id.id}: {e}")

