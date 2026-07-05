# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Manual WhatsApp supplier notifications from supplier-linked documents.

Mirrors the SMS Flow trio in yrp/notification.py (get_flow_sms_context /
send_flow_sms_notification / resend_sms_notification_log) for the WhatsApp
hub-and-spoke transport, reusing that module's contact-resolution helpers
(_get_doc_and_supplier / _get_recipient_details / _extract_numbers /
_doc_field_options). Meta only accepts an APPROVED template for a proactive,
business-initiated message, so the picker lists only APPROVED templates that
are ALSO configured as applicable to the doctype (YRP WhatsApp Template >
Applicable DocTypes) -- the template-centric model this module was refactored
onto (mirrors the e-Way Bill / SMS settings-allowlist pattern): YRP WhatsApp
Hub Settings.enabled_doctypes gates WHICH doctypes may send at all, while each
template's own applicable_doctypes says WHICH templates that doctype may use.
There is no more per-(doctype, template) config row -- header/media choice
and variable filling happen in the sending dialog, not in stored config.

Send never raises on a delivery failure: yrp/whatsapp.deliver_whatsapp_template
returns a captured result dict, the attempt is written to WhatsApp Notification
Log, and a failure surfaces as a red msgprint so the Failed row is the durable
signal for resend. Spec:
docs/superpowers/specs/2026-07-05-yrp-whatsapp-supplier-notification-design.md
"""

import frappe
from frappe import _

from yrp.notification import (
    _doc_field_options,
    _extract_numbers,
    _get_doc_and_supplier,
    _get_recipient_details,
)
from yrp.yrp.doctype.yrp_whatsapp_hub_settings.yrp_whatsapp_hub_settings import (
    parse_whatsapp_variables,
)

_MEDIA_HEADER_TYPES = ("IMAGE", "DOCUMENT", "VIDEO")


def _resolve_wa_variable(mapping_value, doc):
    """Resolve one variable's mapping value against the source document.

    - A leading "=" makes the rest a literal ("=Ready" -> "Ready").
    - A dotted path walks the Link chain hop by hop with frappe.get_value
      ("supplier.supplier_name" -> Supplier(doc.supplier).supplier_name); any
      empty / non-link hop yields "".
    - Anything else is a plain fieldname on the doc itself (doc.get(field)).

    Unresolved values come back as "" so the user fills them in the dialog.
    The mapping value now comes from the template's OWN sample_values child
    row for that variable number/type (see _sample_mapping_value) rather than
    a per-doctype config row -- the template-centric model puts this on the
    template, not on a separate config doctype.
    """
    if mapping_value is None:
        return ""
    mapping_value = str(mapping_value)
    if mapping_value.startswith("="):
        return mapping_value[1:]
    if "." in mapping_value:
        parts = mapping_value.split(".")
        value = doc.get(parts[0])
        df = frappe.get_meta(doc.doctype).get_field(parts[0])
        for segment in parts[1:]:
            if not value or not df or df.fieldtype not in ("Link", "Dynamic Link"):
                return ""
            linked_doctype = df.options
            value = frappe.get_value(linked_doctype, value, segment)
            df = frappe.get_meta(linked_doctype).get_field(segment)
        return value if value not in (None, "") else ""
    value = doc.get(mapping_value)
    return value if value not in (None, "") else ""


def _sample_mapping_value(mirror, number, vtype):
    """The template's own Sample Values row for (number, vtype), or None.

    Sample Values (variable_number/variable_type/sample_value) already exist
    on YRP WhatsApp Template for Meta's template-submission examples, and
    _upsert_local_template never touches them on re-sync -- so they are safe,
    user-owned, per-template storage. This refactor reuses that same field as
    the variable's auto-fill source, resolved through _resolve_wa_variable's
    fieldname / dotted-path / literal ("=...") syntax -- the direct
    replacement for the old per-(doctype, template) variable_mapping JSON.
    """
    for row in (mirror.sample_values or []):
        if row.variable_number == number and row.variable_type == vtype:
            return row.sample_value
    return None


def _template_context(mirror, doc):
    """Build one template's dialog payload: identity fields the dialog needs
    to render the message plus every positional {{n}} variable (body first,
    then a TEXT header's), each pre-resolved from `doc` via the template's own
    Sample Values, or blank for the user to fill."""
    variables = []
    for n in parse_whatsapp_variables(mirror.body_text):
        variables.append({
            "name": "{{%d}}" % n,
            "type": "body",
            "value": _resolve_wa_variable(_sample_mapping_value(mirror, n, "body"), doc),
        })
    if mirror.header_type == "TEXT" and mirror.header_content:
        for n in parse_whatsapp_variables(mirror.header_content):
            variables.append({
                "name": "{{%d}}" % n,
                "type": "header",
                "value": _resolve_wa_variable(_sample_mapping_value(mirror, n, "header"), doc),
            })
    return {
        "name": mirror.name,
        "template_name": mirror.template_name,
        "language_code": mirror.language_code,
        "header_type": mirror.header_type,
        "body_text": mirror.body_text,
        "footer_text": mirror.footer_text,
        "needs_media": mirror.header_type in _MEDIA_HEADER_TYPES,
        "variables": variables,
    }


@frappe.whitelist()
def get_whatsapp_context(doctype, docname, supplier_key="supplier"):
    """Everything the Send-WhatsApp dialog needs: the supplier's recipient
    details, every contact number, and every APPROVED WhatsApp template that
    lists this doctype in its Applicable DocTypes -- each with its positional
    {{n}} body+header variables pre-resolved from the document (unresolved
    ones blank for the user to fill). Meta renders the message from the
    approved template + ordered variables, so no rendered body is returned.

    Throws first if the doctype itself is not in YRP WhatsApp Hub Settings'
    Enabled DocTypes allowlist -- before any supplier/contact resolution, the
    same "fail fast on a disabled route" shape as the SMS/e-Way Bill
    allowlists. Also throws (like the SMS context) on no supplier / no
    contact / no number / no applicable-and-APPROVED template.
    """
    frappe.has_permission(doctype, ptype="read", doc=docname, throw=True)

    settings = frappe.get_cached_doc("YRP WhatsApp Hub Settings")
    if not settings.is_doctype_enabled(doctype):
        frappe.throw(
            _("WhatsApp is not enabled for {0} in YRP WhatsApp Hub Settings").format(_(doctype))
        )

    doc, supplier = _get_doc_and_supplier(doctype, docname, supplier_key)
    details = _get_recipient_details(supplier)
    numbers = _extract_numbers(details["contact"], details["mobile"])

    mirror_names = frappe.get_all(
        "YRP WhatsApp Template", filters={"status": "APPROVED"}, pluck="name"
    )
    templates = []
    for name in mirror_names:
        mirror = frappe.get_doc("YRP WhatsApp Template", name)
        if mirror.is_applicable_for(doctype):
            templates.append(_template_context(mirror, doc))

    if not templates:
        frappe.throw(
            _("No APPROVED WhatsApp template configured for {0}").format(_(doctype))
        )

    return {
        "supplier": supplier.name,
        "contact": details["contact"],
        "mobile": details["mobile"],
        "email": details["email"],
        "numbers": numbers,
        "templates": templates,
        "doc_fields": _doc_field_options(doctype),
    }


def _ordered_list(positional):
    """Values of a positional-string-keyed dict ("1".."10") in ascending int
    order (string sort would put "10" before "2"). A list/tuple passes through;
    None / empty -> []."""
    if positional is None:
        return []
    if isinstance(positional, (list, tuple)):
        return list(positional)
    return [positional[k] for k in sorted(positional, key=lambda k: int(k))]


def _split_params(params):
    """Split the dialog's filled variables into ordered (header_vars, body_vars)
    lists. `params` is a FLAT dict keyed "<type>:<n>" — type in {"body","header"},
    n a positional int as a string — e.g. {"body:1": "PO-001", "body:2": "Acme",
    "header:1": "Invoice"} (or a JSON string of the same, or None). Each key is
    split on ":", bucketed by its type prefix, and each bucket ordered by int(n)
    independently (header and body carry separate Meta positional numbering)."""
    if isinstance(params, str):
        params = frappe.parse_json(params) if params.strip() else {}
    params = params or {}
    buckets = {"header": {}, "body": {}}
    for key, value in params.items():
        vtype, _sep, num = str(key).partition(":")
        if num and vtype in buckets:
            buckets[vtype][num] = value
    return _ordered_list(buckets["header"]), _ordered_list(buckets["body"])


def _build_media_header_source(header_type, header_file):
    """The dialog's uploaded file (`header_file`, a File file_url) becomes the
    deliver-shape header_source dict for a media-header template: {header_format,
    file_url}. None when the template's header isn't IMAGE/DOCUMENT/VIDEO, or no
    file was supplied -- a text-header (or headerless) template never attaches
    media, matching Meta's own component rules."""
    if not header_file or header_type not in _MEDIA_HEADER_TYPES:
        return None
    return {"header_format": header_type, "file_url": header_file}


def _as_text(value):
    return "" if value is None else str(value)


def _render_body_preview(mirror_name, body_vars):
    """Human-readable body with positional {{n}} substituted — the WhatsApp
    value-add over the SMS Flow log (which stored only the param map). Survives
    a since-deleted (or never-found) mirror by returning ""."""
    if not mirror_name:
        return ""
    body = frappe.db.get_value("YRP WhatsApp Template", mirror_name, "body_text") or ""
    for i, val in enumerate(body_vars or [], start=1):
        body = body.replace("{{%d}}" % i, _as_text(val))
    return body


def _default_account_name():
    """Best-effort default hub account name, for the audit log only -- never
    raises. Actual routing always passes account_name=None to
    deliver_whatsapp_template, which resolves the default account itself; this
    is purely so WhatsApp Notification Log records which account a send used."""
    try:
        return frappe.get_cached_doc("YRP WhatsApp Hub Settings").get_default_account_name()
    except Exception:
        return None


@frappe.whitelist()
def send_whatsapp_notification(doctype, docname, template_name, language_code,
        params=None, mobile_no=None, header_file=None, supplier_key="supplier"):
    """Send one APPROVED WhatsApp template to the doc's supplier and log the
    attempt. The lookup is keyed on (template_name, language_code) — never the
    "en" default, because a wrong language is a different / nonexistent Meta
    template. `params` (a FLAT {"body:1": ..., "header:1": ...} dict) is split
    into ordered header/body variable lists; `header_file` (a File file_url
    from an upload in the dialog) becomes the media header_source when the
    template's header_type is IMAGE/DOCUMENT/VIDEO. Every attempt — success or
    failure — is written to WhatsApp Notification Log; a failure surfaces as a
    red msgprint, never a throw, so the Failed row is the durable signal for
    resend. Returns the result dict so a /web modal can gate on result.ok (a
    red msgprint on an HTTP 200 is dropped by the SPA's fetch wrapper) —
    mirrors send_flow_sms_notification's contract.
    """
    frappe.has_permission(doctype, ptype="write", doc=docname, throw=True)
    doc, supplier = _get_doc_and_supplier(doctype, docname, supplier_key)
    details = _get_recipient_details(supplier)
    number = (mobile_no or details["mobile"]).strip()

    header_vars, body_vars = _split_params(params)

    mirror_name = frappe.db.get_value(
        "YRP WhatsApp Template",
        {"template_name": template_name, "language_code": language_code},
        "name",
    )
    if not mirror_name:
        frappe.throw(
            _("WhatsApp template {0} ({1}) not found").format(template_name, language_code)
        )
    mirror = frappe.get_doc("YRP WhatsApp Template", mirror_name)

    # Same governance gate as get_whatsapp_context: write permission on the doc
    # is NOT enough on its own -- without this, any write-permitted user could
    # fire ANY approved template at ANY doctype, bypassing both the Hub
    # Settings allowlist and the template's own applicable_doctypes.
    settings = frappe.get_cached_doc("YRP WhatsApp Hub Settings")
    if not settings.is_doctype_enabled(doctype):
        frappe.throw(
            _("WhatsApp is not enabled for {0} in YRP WhatsApp Hub Settings").format(_(doctype))
        )
    if not mirror.is_applicable_for(doctype):
        frappe.throw(
            _("Template {0} is not applicable for {1}").format(template_name, _(doctype))
        )

    # header_file is a bare client-supplied file_url with no inherent access
    # check -- without this, a user could pass ANY private File's URL and have
    # its bytes read and sent out over WhatsApp regardless of read permission.
    if header_file:
        file_doc = frappe.get_doc("File", {"file_url": header_file})
        file_doc.check_permission("read")

    header_dict = _build_media_header_source(mirror.header_type, header_file)

    account_name = _default_account_name()
    from yrp.whatsapp import deliver_whatsapp_template
    result = deliver_whatsapp_template(
        account_name=account_name,
        to_number=number,
        template_name=template_name,
        language_code=language_code,
        header_vars=header_vars or None,
        body_vars=body_vars or None,
        header_source=header_dict,
    )

    from yrp.yrp.doctype.whatsapp_notification_log.whatsapp_notification_log import (
        create_whatsapp_log,
    )
    create_whatsapp_log(
        reference_doctype=doctype,
        reference_name=docname,
        supplier=supplier.name,
        contact=details["contact"],
        mobile_no=number,
        account=account_name,
        message_type="Media" if header_dict else "Template",
        template=mirror_name,
        template_name=template_name,
        language_code=language_code,
        message=_render_body_preview(mirror_name, body_vars),
        message_variables={"header_vars": header_vars, "body_vars": body_vars},
        header_source=header_dict,
        result=result,
    )

    if result["ok"]:
        frappe.msgprint(_("WhatsApp message sent to {0}").format(number))
    else:
        frappe.msgprint(
            _("WhatsApp to {0} failed: {1}").format(
                number, result.get("error") or result.get("http_status")
            ),
            indicator="red",
        )

    return result


@frappe.whitelist()
def resend_whatsapp_notification_log(log_name):
    """Re-send a logged WhatsApp attempt to the same number, rebuilding the send
    from the row's stored message_variables / header_source / (template_name,
    language_code) and patching that same row in place."""
    log = frappe.get_doc("WhatsApp Notification Log", log_name)
    # Deleted-reference guard FIRST: block before any send. Without it an
    # Administrator resend would fire the WhatsApp and only then fail on a
    # dangling reference (mirrors the SMS resend guard).
    if not frappe.db.exists(log.reference_doctype, log.reference_name):
        frappe.throw(
            _("Cannot resend: {0} {1} no longer exists").format(
                log.reference_doctype, log.reference_name
            )
        )
    frappe.has_permission(log.reference_doctype, ptype="write",
        doc=log.reference_name, throw=True)

    # Same allowlist gate as send: a doctype pulled off the allowlist after
    # the log row was written must not still be resendable.
    settings = frappe.get_cached_doc("YRP WhatsApp Hub Settings")
    if not settings.is_doctype_enabled(log.reference_doctype):
        frappe.throw(
            _("WhatsApp is not enabled for {0} in YRP WhatsApp Hub Settings").format(
                _(log.reference_doctype)
            )
        )

    variables = frappe.parse_json(log.message_variables) if log.message_variables else {}
    header_vars = variables.get("header_vars") or None
    body_vars = variables.get("body_vars") or None
    header_dict = frappe.parse_json(log.header_source) if log.header_source else None

    from yrp.whatsapp import deliver_whatsapp_template
    result = deliver_whatsapp_template(
        account_name=log.account or None,
        to_number=log.mobile_no,
        template_name=log.template_name,
        language_code=log.language_code or "en",
        header_vars=header_vars,
        body_vars=body_vars,
        header_source=header_dict,
    )

    # deliver's `raw` is the hub response (a dict); serialize it before trimming
    # so the audit column update never crashes on a non-string.
    raw = result.get("raw")
    if isinstance(raw, dict):
        raw = frappe.as_json(raw)
    log.status = "Sent" if result["ok"] else "Failed"
    log.meta_message_id = result.get("meta_message_id")
    log.http_status = result.get("http_status")
    log.gateway_response = (raw or "")[:500]
    log.meta_error = result.get("meta_error")
    log.error = result.get("error")
    log.media_id = result.get("media_id")
    log.media_mime = result.get("media_mime")
    log.file_name = result.get("file_name")
    log.sent_at = frappe.utils.now_datetime() if result["ok"] else None
    # ignore_links: an audit-log update must not fail (and roll back a just-sent
    # status) because a linked supplier/contact was deleted after the fact.
    log.flags.ignore_links = True
    log.save(ignore_permissions=True)

    if result["ok"]:
        frappe.msgprint(_("WhatsApp resent to {0}").format(log.mobile_no))
    else:
        frappe.msgprint(
            _("Resend to {0} failed: {1}").format(log.mobile_no, result.get("error")),
            indicator="red",
        )


@frappe.whitelist()
def get_enabled_whatsapp_doctypes():
    """Every doctype enabled to send WhatsApp messages, with its configured
    supplier_key. The Desk/Web JS uses this to decide which doctypes show the
    Send WhatsApp button and which field on the doc to resolve the supplier
    from (Stock Entry's to_supplier/from_supplier logic branches on this).

    Returns {"doctypes": {<reference_doctype>: <supplier_key>, ...}}.
    """
    settings = frappe.get_cached_doc("YRP WhatsApp Hub Settings")
    enabled = settings.get_enabled_doctypes()
    return {"doctypes": {dt: settings.get_supplier_key(dt) for dt in enabled}}
