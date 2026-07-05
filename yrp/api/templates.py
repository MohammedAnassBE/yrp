# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Hub-convention alias for WhatsApp template push (hub -> spoke).

When a ``Hub Connected Site`` on the ``frappe_whatsapp_integration`` hub has no
explicit ``template_push_endpoint``, the hub pushes approved templates to
``{spoke_app_module}.api.templates.receive_push`` (see the hub's
``frappe_whatsapp_hub/api/templates.py::_build_push_url``). With
``spoke_app_module = "yrp"`` that resolves *here*.

This is a thin alias to the canonical handler in
``yrp.api.whatsapp_webhook.receive_push`` so the hub's default convention works
with zero per-site endpoint configuration — mirroring the reference spoke's
``whatsapp_integration/api/templates.py::receive_push`` location. The
``webhook_user`` pin, payload parsing, and upsert all live in the canonical
handler; this only exposes it at the convention path.
"""

import frappe


@frappe.whitelist(allow_guest=False)
def receive_push(**kwargs):
	from yrp.api.whatsapp_webhook import receive_push as _receive_push

	return _receive_push(**kwargs)
