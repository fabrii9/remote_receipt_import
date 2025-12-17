# -*- coding: utf-8 -*-
import base64
import io
import re
import xmlrpc.client
import time
import random
import logging
from datetime import datetime
from collections import defaultdict

from odoo import api, fields, models, _
from odoo.exceptions import UserError

try:
    import openpyxl
except Exception:
    openpyxl = None

import csv


class RemotePaymentImportWizard(models.TransientModel):
    _name = "remote.payment.import.wizard"
    _description = "Importar pagos y crear recibos en Odoo 18"

    upload = fields.Binary(string="Archivo (XLSX o CSV)", required=True)
    filename = fields.Char(string="Nombre de archivo")
    
    # Campos de progreso y estado
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('processing', 'Procesando...'),
        ('done', 'Completado'),
    ], string="Estado", default='draft')
    progress_message = fields.Text(string="Progreso", readonly=True)
    total_rows = fields.Integer(string="Total filas", readonly=True)
    processed_rows = fields.Integer(string="Filas procesadas", readonly=True)

    # -------------------------
    # Helpers de configuración
    # -------------------------
    def _read_settings(self):
        ICP = self.env['ir.config_parameter'].sudo()
        url = ICP.get_param("remote_receipt_import.remote_o18_url")
        db = ICP.get_param("remote_receipt_import.remote_o18_db")
        user = ICP.get_param("remote_receipt_import.remote_o18_user")
        pwd = ICP.get_param("remote_receipt_import.remote_o18_password")
        journal_id = int(ICP.get_param("remote_receipt_import.remote_payment_journal_id") or 0)
        pm_line_id = int(ICP.get_param("remote_receipt_import.remote_payment_method_line_id") or 0)
        tol = float(ICP.get_param("remote_receipt_import.amount_tolerance") or 0.01)
        if not url or not db or not user or not pwd or not journal_id:
            raise UserError(_("Configurar primero en Contabilidad → Importación Remota → Configurar conexión."))
        return url, db, user, pwd, journal_id, pm_line_id, tol

    def _xmlrpc_env(self, url, db, user, pwd):
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, user, pwd, {})
        if not uid:
            raise UserError(_("No se pudo autenticar en Odoo 18 con las credenciales provistas."))
        objects = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
        return uid, objects


    # -------------------------
    # XML-RPC con retry (anti 429 / rate limit)
    # -------------------------
    def _execute_kw_with_retry(
        self,
        objects,
        db,
        uid,
        pwd,
        model,
        method,
        args,
        kwargs=None,
        max_retries=6,
        base_backoff=1.5,
        max_sleep=20.0,
    ):
        """Wrapper centralizado para execute_kw con reintentos ante HTTP 429."""
        kwargs = kwargs or {}
        attempt = 0
        while True:
            try:
                return objects.execute_kw(db, uid, pwd, model, method, args, kwargs)
            except xmlrpc.client.ProtocolError as e:
                # 429 Too Many Requests
                if getattr(e, "errcode", None) == 429 and attempt < max_retries:
                    attempt += 1
                    delay = min(max_sleep, base_backoff * attempt) + random.uniform(0, 0.4)
                    _logger = logging.getLogger(__name__)
                    _logger.warning(
                        "XML-RPC 429 en %s.%s intento=%s/%s; durmiendo %.2fs",
                        model, method, attempt, max_retries, delay
                    )
                    time.sleep(delay)
                    continue
                raise


    # -------------------------
    # Idempotencia (evitar duplicados)
    # -------------------------
    def _make_idempotency_key(
        self,
        journal_id: int,
        company_id: int,
        partner_id: int,
        amount: float,
        date_str: str,
        memo_raw: str,
    ) -> str:
        """Genera una clave estable para identificar un recibo.

        Motivo: con rate-limit (HTTP 429) y/o ejecuciones concurrentes, el wizard puede
        intentar crear el mismo pago más de una vez. Esta clave se graba en `ref` y se
        usa para buscar pagos ya creados.
        """
        memo = (memo_raw or "").strip()
        memo_norm = re.sub(r"\s+", " ", memo)[:120]
        return f"RRI|j{int(journal_id)}|c{int(company_id)}|p{int(partner_id)}|a{amount:.2f}|d{date_str}|m{memo_norm}"[:250]


    def _find_existing_payment(self, objects, db, uid, pwd, ctx, idem_key: str):
        """Busca un payment existente por ref=idempotency_key."""
        recs = self._execute_kw_with_retry(
            objects,
            db,
            uid,
            pwd,
            "account.payment",
            "search_read",
            [[("ref", "=", idem_key)]],
            {"fields": ["id", "state"], "limit": 1, "context": ctx},
        )
        return recs[0] if recs else None

    def _batch_search_partners(self, objects, db, uid, pwd, ctx, cuit_variants_list, journal_company_id):
        """Busca múltiples partners en una sola llamada XML-RPC.
        
        Args:
            cuit_variants_list: lista de tuplas [(cuit_norm, [variants]), ...]
            journal_company_id: ID de la compañía del diario para priorizar partners
        
        Returns:
            dict: {cuit_normalizado: partner_data o None}
        """
        # Aplanar todas las variantes y crear dominio OR masivo
        all_variants = []
        variant_to_original = {}  # mapeo de variante a CUIT normalizado original
        
        for cuit_norm, variants in cuit_variants_list:
            for v in variants:
                all_variants.append(v)
                variant_to_original[v] = cuit_norm
        
        if not all_variants:
            return {}
        
        # Crear dominio OR para todas las variantes
        clauses = []
        for v in all_variants:
            clauses.extend([
                ("vat", "=", v),
                ("ref", "=", v),
                ("commercial_partner_id.vat", "=", v),
            ])
        domain = ["|"] * (len(clauses) - 1) + clauses if clauses else [("id", "=", 0)]
        
        # Búsqueda batch
        partner_ids = self._execute_kw_with_retry(
            objects, db, uid, pwd, "res.partner", "search",
            [domain],
            {"limit": 200, "context": ctx}
        )
        
        # Fallback ILIKE si no encontró por igualdad
        if not partner_ids:
            clauses_ilike = []
            for v in all_variants:
                clauses_ilike.extend([
                    ("vat", "ilike", v),
                    ("ref", "ilike", v),
                    ("commercial_partner_id.vat", "ilike", v),
                ])
            domain_ilike = ["|"] * (len(clauses_ilike) - 1) + clauses_ilike if clauses_ilike else [("id", "=", 0)]
            partner_ids = self._execute_kw_with_retry(
                objects, db, uid, pwd, "res.partner", "search",
                [domain_ilike],
                {"limit": 200, "context": ctx}
            )
        
        if not partner_ids:
            return {cuit: None for cuit, _ in cuit_variants_list}
        
        # Leer todos los partners encontrados
        partners_data = self._execute_kw_with_retry(
            objects, db, uid, pwd, "res.partner", "read",
            [partner_ids, ["name", "company_id", "vat", "ref"]],
            {"context": ctx}
        )
        
        # Helper para extraer ID de campo many2one
        def _m2o_id(val):
            if isinstance(val, (list, tuple)) and val:
                return val[0]
            if isinstance(val, int):
                return val
            return False
        
        # Agrupar partners por CUIT normalizado
        cuit_to_partners = {}
        for p in partners_data:
            pvat = self._normalize_cuit(p.get("vat"))
            pref = self._normalize_cuit(p.get("ref"))
            
            for variant in all_variants:
                vnorm = self._normalize_cuit(variant)
                if vnorm and (vnorm == pvat or vnorm == pref):
                    original_cuit = variant_to_original.get(variant)
                    if original_cuit:
                        if original_cuit not in cuit_to_partners:
                            cuit_to_partners[original_cuit] = []
                        cuit_to_partners[original_cuit].append(p)
                    break
        
        # Elegir el partner correcto por compañía (mismo algoritmo que antes)
        result = {}
        for cuit_norm, _ in cuit_variants_list:
            partners_list = cuit_to_partners.get(cuit_norm, [])
            if not partners_list:
                result[cuit_norm] = None
                continue
            
            # Elegir partner por compañía del diario (o sin compañía)
            chosen = None
            fallback_none_company = None
            for p in partners_list:
                cid = _m2o_id(p.get("company_id"))
                if cid == journal_company_id:
                    chosen = p
                    break
                if not cid and not fallback_none_company:
                    fallback_none_company = p
            
            if not chosen:
                chosen = fallback_none_company or partners_list[0]
            
            result[cuit_norm] = chosen
        
        return result


    # -------------------------
    # Normalizadores / parsing
    # -------------------------
    def _normalize_cuit(self, raw):
        """Devuelve solo dígitos; maneja números y notación científica de Excel."""
        if raw is None:
            return ""
        if isinstance(raw, (int, float)):
            try:
                as_int = int(round(float(raw)))
                return str(as_int)
            except Exception:
                return re.sub(r"\D", "", str(raw))
        s = str(raw).strip()
        try:
            # Notación científica como "1.23254E+11"
            if 'e' in s.lower():
                num = float(s.replace(",", "."))
                return str(int(round(num)))
        except Exception:
            pass
        return re.sub(r"\D", "", s)

    def _vat_variants(self, cuit_digits):
        """
        Variantes comunes para buscar el CUIT/DNI:
        - solo dígitos
        - CUIT con guiones: XX-XXXXXXXX-X
        - DNI con puntos: XX.XXX.XXX (si tiene 8 dígitos)
        """
        s = re.sub(r"\D", "", str(cuit_digits or ""))
        if not s:
            return []
        variants = {s}
        if len(s) == 11:  # CUIT
            variants.add(f"{s[:2]}-{s[2:10]}-{s[10:]}")
        if len(s) == 8:   # DNI
            variants.add(f"{s[:2]}.{s[2:5]}.{s[5:]}")
        return list(variants)

    def _parse_amount(self, raw):
        if raw is None:
            return 0.0
        if isinstance(raw, (int, float)):
            return float(raw)
        s = str(raw).strip()
        if "," in s and "." not in s:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(" ", "").replace(",", "")
        try:
            return float(s)
        except Exception:
            return 0.0

    def _parse_date(self, raw):
        if not raw:
            return fields.Date.context_today(self)
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, (float, int)) and openpyxl:
            try:
                from openpyxl.utils.datetime import from_excel
                return from_excel(raw).date()
            except Exception:
                pass
        s = str(raw).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except Exception:
                continue
        return fields.Date.context_today(self)

    # -------------------------
    # Lectura del archivo
    # -------------------------
    def _read_rows(self, content, filename):
        """
        Devuelve lista de dicts con llaves:
        fecha_pago, tipo_operacion (CUIT/DNI), operacion_relacionada (para memo), importe

        ⚠️ Mapeo correcto:
        - CUIT/DNI = **Tipo de Operación**
        - MEMO     = **Operación Relacionada**
        """
        name = (filename or "").lower()
        rows = []
        if name.endswith((".xlsx", ".xlsm", ".xltx", ".xltm")):
            if not openpyxl:
                raise UserError(_("Falta dependencia 'openpyxl' para leer archivos .xlsx"))
            wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
            ws = wb.active
            headers = [str(c.value).strip() if c.value is not None else "" for c in next(ws.iter_rows(min_row=1, max_row=1))]

            def find_col(name_part):
                for idx, h in enumerate(headers):
                    if name_part.lower() in h.lower():
                        return idx
                return None

            c_fecha = find_col("de Pago") or find_col("fecha")
            c_tipo = find_col("Tipo de Operación") or find_col("tipo")
            c_rel = find_col("Operación Relacionada") or find_col("relacionada")
            c_imp = find_col("Importe") or find_col("mporte")

            if c_tipo is None:
                raise UserError(_("No se encontró la columna 'Tipo de Operación' (CUIT/DNI)."))
            if c_imp is None:
                raise UserError(_("No se encontró la columna 'Importe'."))

            for r in ws.iter_rows(min_row=2):
                tipo_val = r[c_tipo].value if c_tipo is not None else None   # CUIT/DNI
                rel_val = r[c_rel].value if c_rel is not None else None     # MEMO
                vals = {
                    "fecha_pago": self._parse_date(r[c_fecha].value) if c_fecha is not None else fields.Date.context_today(self),
                    "tipo_operacion": (tipo_val or ""),             # crudo para mostrar
                    "operacion_relacionada": rel_val,               # crudo para memo
                    "importe": self._parse_amount(r[c_imp].value),
                }
                if (not str(vals["tipo_operacion"]).strip()) and (not vals["importe"]):
                    continue
                rows.append(vals)
        else:
            text = io.StringIO(content.decode("utf-8", errors="ignore"))
            reader = csv.DictReader(text)

            def pick(d, *keys):
                for k in keys:
                    if k in d:
                        return d[k]
                    for dk in d.keys():
                        if k.lower() == dk.lower():
                            return d[dk]
                return None

            for d in reader:
                tipo_val = pick(d, "Tipo de Operación", "Tipo", "Operacion", "Operación")  # CUIT/DNI
                rel_val = pick(d, "Operación Relacionada", "Operacion Relacionada")        # MEMO
                vals = {
                    "fecha_pago": self._parse_date(pick(d, "de Pago", "Fecha de Pago", "Fecha")),
                    "tipo_operacion": (tipo_val or ""),
                    "operacion_relacionada": rel_val,
                    "importe": self._parse_amount(pick(d, "Importe", "Monto", "Total")),
                }
                if (not str(vals["tipo_operacion"]).strip()) and (not vals["importe"]):
                    continue
                rows.append(vals)
        return rows

    # -------------------------
    # Proceso principal
    # -------------------------
    def action_process(self):
        self.ensure_one()
        if not self.upload:
            raise UserError(_("Subí un archivo primero."))

        url, db, user, pwd, journal_id, pm_line_id, tolerance = self._read_settings()
        uid, objects = self._xmlrpc_env(url, db, user, pwd)

        # Determinar compañía del diario y contextos
        j_read = self._execute_kw_with_retry(objects, db, uid, pwd, "account.journal", "read", [[journal_id], ["company_id"]])
        if not j_read:
            raise UserError(_("No se pudo leer el diario ID %s en Odoo 18.") % journal_id)
        company_field = j_read[0].get("company_id")
        journal_company_id = company_field[0] if isinstance(company_field, (list, tuple)) else (company_field or False)
        if not journal_company_id:
            raise UserError(_("El diario configurado no tiene compañía asignada."))

        try:
            all_company_ids = self._execute_kw_with_retry(objects, db, uid, pwd, "res.company", "search", [[]])
        except Exception:
            all_company_ids = [journal_company_id]

        ctx_any_company = {"active_test": False, "allowed_company_ids": all_company_ids}
        ctx_journal_company = {"active_test": False, "allowed_company_ids": [journal_company_id], "force_company": journal_company_id}

        content = base64.b64decode(self.upload)
        rows = self._read_rows(content, self.filename or "")

        # Actualizar estado del wizard
        self.write({
            'state': 'processing',
            'total_rows': len(rows),
            'processed_rows': 0,
            'progress_message': f'Iniciando importación de {len(rows)} filas...'
        })
        self.env.cr.commit()

        log = self.env["remote.payment.import.log"].sudo().create({
            "file_name": self.filename or "archivo",
        })
        # Commit inicial para liberar lock y permitir que otros procesos funcionen
        self.env.cr.commit()

        # Preparar búsqueda batch de partners
        _logger = logging.getLogger(__name__)
        _logger.info(f"Iniciando importación de {len(rows)} filas")
        
        # Notificación al usuario (bus notification)
        try:
            self.env['bus.bus']._sendone(self.env.user.partner_id, 'simple_notification', {
                'type': 'info',
                'title': 'Importación de Pagos',
                'message': f'Procesando {len(rows)} filas. El proceso continuará en segundo plano.',
                'sticky': False,
            })
        except Exception:
            pass  # Si falla la notificación, continuar igual
        
        # Fase 1: Extraer todos los CUITs y buscarlos en batch
        cuit_to_variants = {}  # {cuit_normalizado: [variants]}
        row_to_cuit = {}  # {row_index: cuit_normalizado}
        
        for idx, row in enumerate(rows):
            tipo_raw = row["tipo_operacion"]
            cuit_digits = self._normalize_cuit(tipo_raw)
            variants = self._vat_variants(cuit_digits)
            if variants:
                cuit_to_variants[cuit_digits] = variants
                row_to_cuit[idx] = cuit_digits
        
        # Búsqueda batch de partners (1 sola llamada XML-RPC en lugar de N)
        _logger.info(f"Buscando {len(cuit_to_variants)} partners únicos en batch...")
        cuit_variants_list = [(cuit, vars) for cuit, vars in cuit_to_variants.items()]
        partners_cache = self._batch_search_partners(
            objects, db, uid, pwd, ctx_any_company, cuit_variants_list, journal_company_id
        )
        _logger.info(f"Partners encontrados: {sum(1 for p in partners_cache.values() if p is not None)}")
        
        # Fase 2: Procesar filas con cache de partners
        lines_to_create = []  # Acumular para creación batch
        batch_size = 50  # Crear logs cada 50 filas para liberar memoria
        
        for idx, row in enumerate(rows, start=1):
            fecha = row["fecha_pago"]
            tipo_raw = row["tipo_operacion"]            # AHORA es el CUIT/DNI
            memo_raw = row["operacion_relacionada"]     # para el memo
            importe = float(row["importe"] or 0.0)

            cuit_digits = self._normalize_cuit(tipo_raw)
            line_vals = {
                "log_id": log.id,
                "fecha_pago": fecha,
                "tipo_operacion": str(tipo_raw or ""),
                "operacion_relacionada": str(memo_raw or ""),
                "importe": importe,
                "status": "error",
                "message": "",
            }

            try:
                # Usar cache de partners (ya buscados en batch)
                if cuit_digits not in partners_cache:
                    line_vals.update({
                        "status": "partner_not_found",
                        "message": "No hay CUIT/DNI válido (tomado de 'Tipo de Operación')."
                    })
                    lines_to_create.append(line_vals)
                    continue

                chosen = partners_cache[cuit_digits]
                if not chosen:
                    line_vals.update({
                        "status": "partner_not_found",
                        "message": f"No se encontró partner (vat/ref) para {cuit_digits}."
                    })
                    lines_to_create.append(line_vals)
                    continue

                def _m2o_id(val):
                    if isinstance(val, (list, tuple)) and val:
                        return val[0]
                    if isinstance(val, int):
                        return val
                    return False

                partner_id = chosen["id"] if isinstance(chosen.get("id"), int) else None
                if not partner_id:
                    line_vals.update({
                        "status": "partner_not_found",
                        "message": f"No se pudo determinar el ID del partner para {cuit_digits}."
                    })
                    lines_to_create.append(line_vals)
                    continue

                line_vals["partner_id"] = partner_id
                line_vals["partner_name"] = chosen.get("name")

                # Deuda en la compañía del diario
                aml_domain = [
                    ("partner_id", "=", partner_id),
                    ("account_id.account_type", "=", "asset_receivable"),
                    ("reconciled", "=", False),
                    ("parent_state", "=", "posted"),
                    ("company_id", "=", journal_company_id),
                ]
                aml_ids = self._execute_kw_with_retry(
                    objects, db, uid, pwd, "account.move.line", "search",
                    [aml_domain],
                    {"limit": 0, "context": ctx_journal_company}
                )
                deuda = 0.0
                if aml_ids:
                    aml_read = self._execute_kw_with_retry(
                        objects, db, uid, pwd, "account.move.line", "read",
                        [aml_ids, ["amount_residual"]],
                        {"context": ctx_journal_company}
                    )
                    deuda = sum((l.get("amount_residual") or 0.0) for l in aml_read)

                line_vals["deuda_detectada"] = deuda

                # ¿Coincide dentro de la tolerancia?
                if abs(importe - deuda) <= tolerance:
                    payment_vals = {
                        "payment_type": "inbound",
                        "partner_type": "customer",
                        "partner_id": partner_id,
                        "amount": round(importe, 2),
                        "date": fecha.strftime("%Y-%m-%d") if fecha else fields.Date.today().strftime("%Y-%m-%d"),
                        "journal_id": journal_id,
                        "company_id": journal_company_id,
                        # MEMO desde Operación Relacionada
                        "memo": str(memo_raw or ""),
                    }
                    if pm_line_id:
                        payment_vals["payment_method_line_id"] = pm_line_id

                    payment_id = self._execute_kw_with_retry(
                        objects, db, uid, pwd, "account.payment", "create",
                        [payment_vals],
                        {"context": ctx_journal_company}
                    )

                    # Intentar validar y SIEMPRE chequear el estado real en el server
                    post_error = None
                    state = "draft"
                    try:
                        self._execute_kw_with_retry(
                            objects, db, uid, pwd, "account.payment", "action_post",
                            [[payment_id]],
                            {"context": ctx_journal_company}
                        )
                    except Exception as e_post:
                        post_error = e_post
                    finally:
                        try:
                            pdata = self._execute_kw_with_retry(
                                objects, db, uid, pwd, "account.payment", "read",
                                [[payment_id], ["state"]],
                                {"context": ctx_journal_company}
                            )
                            if pdata:
                                state = (pdata[0].get("state") or state)
                        except Exception:
                            pass

                    if state in ("posted", "in_process"):
                        line_vals.update({
                            "payment_id": payment_id,
                            "status": "approved",
                            "message": f"Pago creado y validado (estado: {state})."
                        })
                    else:
                        msg = f"Pago creado en borrador; no se pudo validar automáticamente (estado: {state})."
                        if post_error:
                            msg += f" Error: {post_error}"
                        line_vals.update({
                            "payment_id": payment_id,
                            "status": "error",
                            "message": msg
                        })
                else:
                    line_vals.update({
                        "status": "mismatch",
                        "message": f"Importe {importe:.2f} distinto de deuda {deuda:.2f}."
                    })

            except Exception as e:
                line_vals.update({
                    "status": "error",
                    "message": f"Excepción: {e}"
                })

            lines_to_create.append(line_vals)
            
            # Crear logs en batch cada 50 filas y hacer commit para liberar worker
            if len(lines_to_create) >= batch_size:
                self.env["remote.payment.import.log.line"].sudo().create(lines_to_create)
                lines_to_create = []
                
                # Actualizar progreso
                progress_pct = int((idx / len(rows)) * 100)
                self.write({
                    'processed_rows': idx,
                    'progress_message': f'Procesadas {idx}/{len(rows)} filas ({progress_pct}%)'
                })
                
                self.env.cr.commit()  # Liberar locks de BD para que otros endpoints funcionen
                _logger.info(f"Procesadas {idx}/{len(rows)} filas, commit realizado")
        
        # Crear líneas restantes
        if lines_to_create:
            self.env["remote.payment.import.log.line"].sudo().create(lines_to_create)
            self.env.cr.commit()
        
        _logger.info(f"Importación completada: {len(rows)} filas procesadas")
        
        # Actualizar estado final
        approved = len([l for l in log.lines_ids if l.status == 'approved'])
        self.write({
            'state': 'done',
            'processed_rows': len(rows),
            'progress_message': f'✓ Importación completada: {len(rows)} filas procesadas\n✓ Pagos aprobados: {approved}\n✓ Revisa los logs para más detalles'
        })
        self.env.cr.commit()
        
        # Notificación final
        try:
            self.env['bus.bus']._sendone(self.env.user.partner_id, 'simple_notification', {
                'type': 'success',
                'title': 'Importación Exitosa',
                'message': f'✓ Completado: {approved} pagos creados de {len(rows)} filas',
                'sticky': True,
            })
        except Exception:
            pass
        
        # Guardar el log_id para el método action_view_logs
        self.env.context = dict(self.env.context, active_log_id=log.id)

        # Retornar acción para recargar el wizard y mostrar estado
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_log_id': log.id}
        }
    
    def action_view_logs(self):
        """Abrir los logs de la importación."""
        self.ensure_one()
        log_id = self.env.context.get('active_log_id')
        if not log_id:
            # Buscar el log más reciente con el mismo nombre de archivo
            log = self.env['remote.payment.import.log'].sudo().search(
                [('file_name', '=', self.filename or 'archivo')],
                order='create_date desc',
                limit=1
            )
            log_id = log.id if log else False
        
        if not log_id:
            raise UserError(_('No se encontró el log de importación.'))
        
        action = self.env.ref('remote_receipt_import.action_remote_payment_import_log').sudo().read()[0]
        action['domain'] = [('id', '=', log_id)]
        action['target'] = 'current'
        return action
