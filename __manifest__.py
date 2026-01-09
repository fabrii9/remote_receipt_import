# -*- coding: utf-8 -*-
{
    "name": "Remote Receipt Import (O16 → O18)",
    "version": "16.0.2.2",
    "summary": "Importación de pagos con arquitectura robusta: vistas normales (no wizards), cola asíncrona, circuit breaker, rate limiting y soporte para pagos parciales (Mercado Pago).",
    "author": "Fabrizio + ChatGPT",
    "license": "AGPL-3",
    "category": "Accounting",
    "depends": ["base", "account", "bus"],
    "data": [
        "security/ir.model.access.csv",
        "data/cron.xml",             # cron para procesamiento asíncrono
        "views/settings_views.xml",  # configuración
        "views/wizard_views.xml",    # wizard de ingesta
        "views/log_views.xml",       # logs históricos
        "views/queue_views.xml"      # dashboard de cola y progreso
    ],
    "external_dependencies": {
        "python": ["openpyxl"]
    },
    "application": True,
    "installable": True,
    "auto_install": False,
    "post_init_hook": None,
}
