# -*- coding: utf-8 -*-
"""
Procesador asíncrono de cola de pagos.
Este módulo contiene la lógica de procesamiento que se ejecuta en background.
"""
import time
import logging
import xmlrpc.client
from datetime import datetime
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from .flow_control import RateLimiter, CircuitBreaker, CircuitOpenError

_logger = logging.getLogger(__name__)


# Instancias globales de control de flujo
# Se comparten entre todas las ejecuciones para proteger el remoto
RATE_LIMITER = RateLimiter(max_requests=5, time_window=1.0)  # 5 req/s
CIRCUIT_BREAKER = CircuitBreaker(
    failure_threshold=10,  # 10 fallos consecutivos
    timeout_duration=300,  # 5 minutos de timeout
    success_threshold=3    # 3 éxitos para recuperar
)


class PaymentImportQueueLineProcessor(models.Model):
    _inherit = "payment.import.queue.line"

    @api.model
    def process_queue_batch(self, batch_id, checkpoint_id, batch_size=30):
        """
        Procesa un lote de registros de la cola.
        
        Este método está diseñado para ser llamado por queue_job o cron.
        Procesa registros pendientes con control de flujo robusto.
        
        Args:
            batch_id: ID del batch/log a procesar
            checkpoint_id: ID del checkpoint para tracking
            batch_size: Cantidad de registros a procesar en esta ejecución
        """
        _logger.info(f"🔄 Iniciando procesamiento: batch={batch_id}, checkpoint={checkpoint_id}, size={batch_size}")
        
        checkpoint = self.env["payment.import.checkpoint"].sudo().browse(checkpoint_id)
        if not checkpoint.exists():
            _logger.error(f"❌ Checkpoint {checkpoint_id} no encontrado")
            return
        
        # Verificar estado del circuit breaker
        breaker_state = CIRCUIT_BREAKER.get_state()
        if breaker_state['state'] == 'open':
            _logger.warning(
                f"⚠️ Circuit breaker OPEN. "
                f"Fallos: {breaker_state['failure_count']}. "
                f"Reprogramando procesamiento..."
            )
            # Reprogramar para más tarde
            if hasattr(self, 'with_delay'):
                self.with_delay(eta=300).process_queue_batch(batch_id, checkpoint_id, batch_size)
            return
        
        # Obtener configuración
        try:
            wizard = self.env["remote.payment.import.wizard"]
            url, db, user, pwd, journal_id, pm_line_id, tolerance = wizard._read_settings()
            uid, objects = wizard._xmlrpc_env(url, db, user, pwd)
        except Exception as e:
            _logger.error(f"❌ Error en configuración: {e}")
            checkpoint.mark_failed(str(e))
            return
        
        # Obtener contextos (igual que antes)
        try:
            j_read = objects.execute_kw(db, uid, pwd, "account.journal", "read", [[journal_id], ["company_id"]])
            if not j_read:
                raise UserError(f"No se pudo leer el diario ID {journal_id}")
            company_field = j_read[0].get("company_id")
            journal_company_id = company_field[0] if isinstance(company_field, (list, tuple)) else company_field
            
            all_company_ids = objects.execute_kw(db, uid, pwd, "res.company", "search", [[]])
        except Exception as e:
            _logger.error(f"❌ Error obteniendo contextos: {e}")
            checkpoint.mark_failed(str(e))
            return
        
        ctx_any_company = {"active_test": False, "allowed_company_ids": all_company_ids}
        ctx_journal_company = {"active_test": False, "allowed_company_ids": [journal_company_id], "force_company": journal_company_id}
        
        # Buscar registros pendientes (con scheduled_date si aplica)
        domain = [
            ('batch_id', '=', batch_id),
            ('state', 'in', ['pending', 'failed']),  # Incluir failed para retry
            '|',
            ('scheduled_date', '=', False),
            ('scheduled_date', '<=', fields.Datetime.now())
        ]
        
        pending_records = self.sudo().search(domain, limit=batch_size, order='priority desc, id asc')
        
        if not pending_records:
            _logger.info(f"✅ No hay registros pendientes en batch {batch_id}")
            checkpoint.mark_completed()
            return
        
        _logger.info(f"📋 Procesando {len(pending_records)} registros...")
        
        processed_count = 0
        success_count = 0
        circuit_broken = False
        
        for record in pending_records:
            if circuit_broken:
                _logger.warning("⚠️ Circuit breaker activado, deteniendo lote")
                break
            
            try:
                # Marcar como en procesamiento
                record.mark_as_processing()
                start_time = time.time()
                
                # Procesar con rate limiting y circuit breaker
                with RATE_LIMITER:
                    with CIRCUIT_BREAKER:
                        self._process_single_record(
                            record, objects, db, uid, pwd,
                            journal_id, journal_company_id, pm_line_id, tolerance,
                            ctx_any_company, ctx_journal_company, wizard
                        )
                
                processing_time = time.time() - start_time
                record.write({'processing_time': processing_time})
                
                # Actualizar checkpoint
                is_success = record.state == 'done'
                checkpoint.update_progress(
                    processed_count=1,
                    success=is_success,
                    failed=(record.state == 'failed'),
                    skipped=(record.state == 'skipped')
                )
                
                if is_success:
                    success_count += 1
                processed_count += 1
                
                # Commit cada 10 registros para liberar locks
                if processed_count % 10 == 0:
                    self.env.cr.commit()
                    _logger.info(f"💾 Checkpoint: {processed_count} registros procesados")
                
            except CircuitOpenError as e:
                _logger.error(f"🔴 Circuit breaker abierto: {e}")
                circuit_broken = True
                record.write({'state': 'pending'})  # Re-encolar
                
            except Exception as e:
                _logger.error(f"❌ Error procesando registro {record.id}: {e}", exc_info=True)
                record.mark_as_failed(f"Error inesperado: {str(e)[:500]}")
                checkpoint.update_progress(processed_count=1, failed=True)
        
        # Commit final
        self.env.cr.commit()
        
        _logger.info(
            f"✅ Lote completado: {processed_count} procesados, "
            f"{success_count} exitosos"
        )
        
        # Si quedan más pendientes, encolar siguiente lote
        remaining = self.sudo().search_count([
            ('batch_id', '=', batch_id),
            ('state', '=', 'pending')
        ])
        
        if remaining > 0 and not circuit_broken:
            _logger.info(f"⏭️ Quedan {remaining} registros, encolando siguiente lote...")
            if hasattr(self, 'with_delay'):
                # Pequeño delay entre lotes para no saturar
                self.with_delay(eta=5, priority=5).process_queue_batch(
                    batch_id, checkpoint_id, batch_size
                )
            else:
                _logger.info("ℹ️ queue_job no disponible, siguiente lote será procesado por cron")
        elif remaining == 0:
            _logger.info(f"🎉 Batch {batch_id} completado totalmente!")
            checkpoint.mark_completed()

    def _process_single_record(self, record, objects, db, uid, pwd,
                               journal_id, journal_company_id, pm_line_id, tolerance,
                               ctx_any_company, ctx_journal_company, wizard):
        """Procesa un solo registro de la cola."""
        
        import json
        row_data = json.loads(record.row_data) if record.row_data else {}
        
        tipo_raw = record.tipo_operacion
        memo_raw = record.operacion_relacionada
        importe = record.importe
        fecha = record.fecha_pago
        
        cuit_digits = wizard._normalize_cuit(tipo_raw)
        variants = wizard._vat_variants(tipo_raw, cuit_digits)
        
        if not variants:
            record.mark_as_skipped("No hay CUIT/DNI válido")
            return
        
        # Buscar partner (misma lógica que antes)
        clauses = []
        for v in variants:
            clauses.extend([
                ("vat", "=", v),
                ("ref", "=", v),
                ("commercial_partner_id.vat", "=", v),
            ])
        domain = ["|"] * (len(clauses) - 1) + clauses if clauses else [("id", "=", 0)]
        
        partner_ids_all = objects.execute_kw(
            db, uid, pwd, "res.partner", "search",
            [domain],
            {"limit": 10, "context": ctx_any_company}
        )
        
        # Fallback ILIKE
        if not partner_ids_all:
            clauses_ilike = []
            for v in variants:
                clauses_ilike.extend([
                    ("vat", "ilike", v),
                    ("ref", "ilike", v),
                    ("commercial_partner_id.vat", "ilike", v),
                ])
            domain_ilike = ["|"] * (len(clauses_ilike) - 1) + clauses_ilike if clauses_ilike else [("id", "=", 0)]
            partner_ids_all = objects.execute_kw(
                db, uid, pwd, "res.partner", "search",
                [domain_ilike],
                {"limit": 10, "context": ctx_any_company}
            )
        
        if not partner_ids_all:
            record.mark_as_skipped(f"Partner no encontrado para CUIT {cuit_digits}")
            return
        
        # Leer y elegir partner
        partners_data = objects.execute_kw(
            db, uid, pwd, "res.partner", "read",
            [partner_ids_all, ["name", "company_id"]],
            {"context": ctx_any_company}
        )
        
        def _m2o_id(val):
            if isinstance(val, (list, tuple)) and val:
                return val[0]
            if isinstance(val, int):
                return val
            return False
        
        chosen = None
        fallback_none_company = None
        for p in partners_data:
            cid = _m2o_id(p.get("company_id"))
            if cid == journal_company_id:
                chosen = p
                break
            if not cid and not fallback_none_company:
                fallback_none_company = p
        if not chosen:
            chosen = fallback_none_company or partners_data[0]
        
        partner_id = chosen["id"]
        partner_name = chosen.get("name")
        
        # Verificar deuda
        aml_domain = [
            ("partner_id", "=", partner_id),
            ("account_id.account_type", "=", "asset_receivable"),
            ("reconciled", "=", False),
            ("parent_state", "=", "posted"),
            ("company_id", "=", journal_company_id),
        ]
        aml_ids = objects.execute_kw(
            db, uid, pwd, "account.move.line", "search",
            [aml_domain],
            {"limit": 0, "context": ctx_journal_company}
        )
        
        deuda = 0.0
        if aml_ids:
            aml_read = objects.execute_kw(
                db, uid, pwd, "account.move.line", "read",
                [aml_ids, ["amount_residual"]],
                {"context": ctx_journal_company}
            )
            deuda = sum((l.get("amount_residual") or 0.0) for l in aml_read)
        
        # Verificar tolerancia
        if abs(importe - deuda) > tolerance:
            record.mark_as_skipped(f"Mismatch: importe {importe:.2f} vs deuda {deuda:.2f}")
            return
        
        # Crear payment
        payment_vals = {
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": partner_id,
            "amount": round(importe, 2),
            "date": fecha.strftime("%Y-%m-%d") if fecha else fields.Date.today().strftime("%Y-%m-%d"),
            "journal_id": journal_id,
            "company_id": journal_company_id,
            "memo": str(memo_raw or ""),
        }
        if pm_line_id:
            payment_vals["payment_method_line_id"] = pm_line_id
        
        payment_id = objects.execute_kw(
            db, uid, pwd, "account.payment", "create",
            [payment_vals],
            {"context": ctx_journal_company}
        )
        
        # Validar payment
        try:
            objects.execute_kw(
                db, uid, pwd, "account.payment", "action_post",
                [[payment_id]],
                {"context": ctx_journal_company}
            )
        except Exception:
            pass  # No importa si falla el post
        
        # Verificar estado
        pdata = objects.execute_kw(
            db, uid, pwd, "account.payment", "read",
            [[payment_id], ["state"]],
            {"context": ctx_journal_company}
        )
        state = pdata[0].get("state", "draft") if pdata else "draft"
        
        if state in ("posted", "in_process"):
            record.mark_as_done(partner_id=partner_id, partner_name=partner_name, payment_id=payment_id)
        else:
            record.mark_as_failed(f"Payment creado pero no validado (estado: {state})")
