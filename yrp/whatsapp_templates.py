# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _

from yrp import whatsapp_hub_client as hub_client

_META_STATUS_MAP = {
    "PENDING": "IN_REVIEW",
    "FLAGGED": "REJECTED",
    "DRAFT": "DRAFT",
    "IN_REVIEW": "IN_REVIEW",
    "APPROVED": "APPROVED",
    "REJECTED": "REJECTED",
    "PAUSED": "PAUSED",
    "DISABLED": "DISABLED",
}


def _normalize_meta_status(s):
    """Map a Meta/hub status string onto the local mirror's status set.
    Missing -> DRAFT; anything unrecognised -> IN_REVIEW (never trusted raw)."""
    if not s:
        return "DRAFT"
    return _META_STATUS_MAP.get(s.upper(), "IN_REVIEW")


@frappe.whitelist()
def sync_templates_from_hub(account_name=None):
    """Sync approved templates from Meta via the WhatsApp Hub into the local mirror.

    Gated to System Manager: the reference left the analogous endpoint
    role-ungated, but this call drives credential-backed hub traffic and
    overwrites the mirror, so only a System Manager may trigger it.

    This is also a daily scheduler_events entry, so a disabled/unconfigured
    hub must NOT raise (that would write an Error Log every day the feature
    is dormant) — it degrades to a benign skipped result instead.
    """
    frappe.only_for("System Manager")

    if not hub_client.hub_enabled():
        return {"skipped": True, "reason": "WhatsApp hub disabled"}

    account_name = hub_client._get_account_name(account_name)

    try:
        result = hub_client.sync_templates_from_meta(account_name)
        templates_data = result.get("data", []) if isinstance(result, dict) else []
        synced = 0
        for template_data in templates_data:
            _upsert_local_template(template_data, account_name)
            synced += 1
        return {
            "message": _("Synced {0} templates via hub").format(synced),
            "synced_count": synced,
        }
    except Exception as e:
        frappe.throw(_("Error syncing templates via hub: {0}").format(str(e)))


def _upsert_local_template(template_data, whatsapp_account):
    """Upsert one Meta-shape template dict into YRP WhatsApp Template.

    Upsert key = template_id (the Meta id). Re-syncs BODY/HEADER/FOOTER/BUTTONS
    components. Every write sets doc.flags.from_meta_sync=True so the mirror's
    validate() keeps Meta's real status instead of forcing DRAFT. Returns the
    upserted doc's name (docname).
    """
    existing = frappe.db.get_value(
        "YRP WhatsApp Template", {"template_id": template_data.get("id")}, "name"
    )

    if existing:
        doc = frappe.get_doc("YRP WhatsApp Template", existing)
        # applicable_doctypes is pure user config (which enabled DocTypes may
        # send this template) -- never Meta-sourced, so it must survive a
        # re-sync untouched. Snapshot it before any field below is touched and
        # explicitly re-attach it after, rather than relying on this loop
        # simply never mentioning the field (the way `buttons` is rebuilt
        # in-place shows how easy it is to accidentally wipe a child table
        # here in a future edit).
        preserved_applicable_doctypes = [
            row.as_dict() for row in (doc.applicable_doctypes or [])
        ]

        doc.status = _normalize_meta_status(template_data.get("status"))

        for component in template_data.get("components", []):
            comp_type = component.get("type")
            if comp_type == "BODY":
                doc.body_text = component.get("text", "")
            elif comp_type == "HEADER":
                doc.header_type = component.get("format", "TEXT")
                if doc.header_type == "TEXT":
                    doc.header_content = component.get("text", "")
            elif comp_type == "FOOTER":
                doc.footer_text = component.get("text", "")
            elif comp_type == "BUTTONS":
                doc.buttons = []
                for btn in component.get("buttons", []):
                    doc.append("buttons", {
                        "button_type": btn.get("type", "QUICK_REPLY"),
                        "button_text": btn.get("text", ""),
                        "button_url": btn.get("url", ""),
                        "phone_number": btn.get("phone_number", ""),
                    })

        doc.set("applicable_doctypes", [])
        for row in preserved_applicable_doctypes:
            doc.append("applicable_doctypes", row)

        doc.flags.from_meta_sync = True
        doc.save(ignore_permissions=True)
        return doc.name

    doc = frappe.get_doc({
        "doctype": "YRP WhatsApp Template",
        "template_name": template_data.get("name"),
        "template_id": template_data.get("id"),
        "language_code": template_data.get("language"),
        "category": template_data.get("category"),
        "status": _normalize_meta_status(template_data.get("status")),
        "whatsapp_account": whatsapp_account,
    })

    for component in template_data.get("components", []):
        comp_type = component.get("type")
        if comp_type == "BODY":
            doc.body_text = component.get("text", "")
        elif comp_type == "HEADER":
            doc.header_type = component.get("format", "TEXT")
            if doc.header_type == "TEXT":
                doc.header_content = component.get("text", "")
        elif comp_type == "FOOTER":
            doc.footer_text = component.get("text", "")
        elif comp_type == "BUTTONS":
            for btn in component.get("buttons", []):
                doc.append("buttons", {
                    "button_type": btn.get("type", "QUICK_REPLY"),
                    "button_text": btn.get("text", ""),
                    "button_url": btn.get("url", ""),
                    "phone_number": btn.get("phone_number", ""),
                })

    doc.flags.from_meta_sync = True
    doc.insert(ignore_permissions=True)
    return doc.name


def get_template_variables(template_name):
    """Positional {{n}} variables of a template — body first, then a TEXT header.

    Each entry is {number, type("body"|"header"), sample_value}, sorted by
    (type, number) so body variables precede header variables, ascending
    within each. sample_value is pulled from the template's sample_values
    child rows (matched on variable_number + variable_type), else "".
    Used by the YRP WhatsApp Template form JS / future consumers.
    """
    template = frappe.get_doc("YRP WhatsApp Template", template_name)

    variables = []

    body_vars = re.findall(r"\{\{(\d+)\}\}", template.body_text or "")
    for var_num in body_vars:
        sample = next(
            (s.sample_value for s in template.sample_values
             if s.variable_number == int(var_num) and s.variable_type == "body"),
            "",
        )
        variables.append({"number": int(var_num), "type": "body", "sample_value": sample})

    if template.header_content:
        header_vars = re.findall(r"\{\{(\d+)\}\}", template.header_content)
        for var_num in header_vars:
            sample = next(
                (s.sample_value for s in template.sample_values
                 if s.variable_number == int(var_num) and s.variable_type == "header"),
                "",
            )
            variables.append({"number": int(var_num), "type": "header", "sample_value": sample})

    return sorted(variables, key=lambda x: (x["type"], x["number"]))
