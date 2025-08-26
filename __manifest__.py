# -*- coding: utf-8 -*-
{
    "name": "Remote Receipt Import (O16 → O18)",
    "version": "16.0.1.2",
    "summary": "Importa pagos desde Excel/CSV y crea recibos en Odoo 18 si el importe coincide con la deuda del cliente.",
    "author": "Fabrizio + ChatGPT",
    "license": "AGPL-3",
    "depends": ["base", "account"],
    "data": [
        "security/ir.model.access.csv",
        "views/settings_views.xml",  # raíz + acción config + menú config
        "views/wizard_views.xml",    # acción wizard + menú wizard
        "views/log_views.xml"        # acción logs + menú logs
    ],
    "external_dependencies": {
        "python": ["openpyxl"]
    },
    "application": True,
    "installable": True
}
