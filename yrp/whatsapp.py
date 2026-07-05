# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Never-raise WhatsApp template delivery. Mirrors sms.py's deliver_* contract:
every outcome — hub success, hub failure, an unresolvable or oversize media
header, or an unexpected exception — is captured in the returned result dict;
this module never raises. Spec:
docs/superpowers/specs/2026-07-05-yrp-whatsapp-supplier-notification-design.md
"""

import base64
import mimetypes

import frappe
from frappe import _

from yrp import whatsapp_hub_client

# Meta Cloud API media caps (bytes) per header format.
_META_MEDIA_CAP = {
    "IMAGE": 5 * 1024 * 1024,        # 5 MB
    "VIDEO": 16 * 1024 * 1024,       # 16 MB
    "DOCUMENT": 100 * 1024 * 1024,   # 100 MB
}
# The base64-over-JSON body hits the hub's request-body cap before Meta's own
# limit. When YRP WhatsApp Hub Settings does not carry an explicit
# hub_max_content_length, assume the hub's default 25 MB. base64 inflates raw
# bytes by ~1.34x, so the raw-byte budget is hub_cap / 1.34.
_HUB_DEFAULT_MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # 25 MB
_BASE64_INFLATION = 1.34


def deliver_whatsapp_template(*, account_name=None, to_number, template_name,
        language_code="en", header_vars=None, body_vars=None, header_source=None) -> dict:
    """Send one approved WhatsApp template via the hub and capture the outcome.

    `header_vars` / `body_vars` are positional-keyed dicts ("1".."10", …) that
    are serialized in ascending int order. `header_source` (dict|None) selects a
    media header: {"header_format": "IMAGE|DOCUMENT|VIDEO", <one file ref>} where
    the ref is `file_url` (a local File), `print_format` + `doctype`/`name`
    (render a PDF), or `attached_to_doctype`/`attached_to_name` (first attachment).
    A media header takes precedence over text `header_vars`.

    Returns {ok, meta_message_id, http_status, raw, error, meta_error, media_id,
    media_mime, file_name}. `ok` is True only when the hub reported success AND a
    meta_message_id came back. Never raises: any failure is captured in the dict.
    """
    result = {"ok": False, "meta_message_id": None, "http_status": None,
        "raw": None, "error": None, "meta_error": None,
        "media_id": None, "media_mime": None, "file_name": None}
    try:
        components = []

        if header_source:
            header = _upload_header(account_name, header_source)
            result["media_id"] = header.get("media_id")
            result["media_mime"] = header.get("media_mime")
            result["file_name"] = header.get("file_name")
            if not header["ok"]:
                result["error"] = header.get("error")
                return result
            fmt = header["header_format"]          # UPPERCASE component format
            key = fmt.lower()                       # hub/Meta media object key
            media_obj = {"id": header["media_id"]}
            if fmt == "DOCUMENT" and header.get("file_name"):
                media_obj["filename"] = header["file_name"]
            components.append({"type": "header",
                "parameters": [{"type": key, key: media_obj}]})
        elif header_vars:
            components.append({"type": "header",
                "parameters": [{"type": "text", "text": _as_text(v)}
                               for v in _ordered_values(header_vars)]})

        if body_vars:
            components.append({"type": "body",
                "parameters": [{"type": "text", "text": _as_text(v)}
                               for v in _ordered_values(body_vars)]})

        # Meta template names are lowercased + underscored (see Global
        # Constraints; mirrors the reference api/templates.py L201).
        template_name = (template_name or "").lower().replace(" ", "_")
        success, resp = whatsapp_hub_client.send_template_message(
            account_name, to_number, template_name, language_code,
            components if components else None)

        if success and isinstance(resp, dict):
            result["raw"] = frappe.as_json(resp) if isinstance(resp, (dict, list)) else (resp or "")
            result["http_status"] = resp.get("status_code")
            result["meta_message_id"] = resp.get("meta_message_id")
            result["ok"] = bool(result["meta_message_id"])
            if not result["ok"]:
                result["error"] = _("Hub reported success but returned no meta_message_id")
        elif isinstance(resp, dict):
            # Meta-failure path: the hub client returns the FULL hub dict
            # (success False) so we can log meta_error + http_status. `error`
            # stays a plain string for send_whatsapp_notification's red msgprint.
            result["raw"] = frappe.as_json(resp) if isinstance(resp, (dict, list)) else (resp or "")
            result["http_status"] = resp.get("status_code")
            result["meta_error"] = resp.get("meta_error")
            result["error"] = resp.get("error") or _("Unknown hub error")
        else:
            # transport-exception path: the hub client returns str(e)
            result["error"] = resp
    except Exception as e:
        result["error"] = str(e)
        frappe.log_error(frappe.get_traceback(), "deliver_whatsapp_template failed")
    return result


def _upload_header(account_name, header_source) -> dict:
    """Resolve the header media, enforce the size cap, base64-encode, and upload
    via the hub. Returns {ok, media_id, media_mime, file_name, header_format,
    error}. Never raises: any failure is captured in the dict."""
    out = {"ok": False, "media_id": None, "media_mime": None,
        "file_name": None, "header_format": None, "error": None}
    try:
        header_format = (header_source.get("header_format") or "").upper()
        if header_format not in _META_MEDIA_CAP:
            out["error"] = _("Unsupported header_format: {0}").format(header_format or "None")
            return out
        out["header_format"] = header_format

        content, mime, filename = _resolve_header_bytes(header_source)
        if content is None:
            out["error"] = _("Header source has no resolvable file")
            return out
        out["media_mime"] = mime
        out["file_name"] = filename

        cap = _raw_size_cap(header_format)
        if len(content) > cap:
            out["error"] = _("Header file {0} ({1} bytes) exceeds the {2} byte limit").format(
                filename, len(content), cap)
            return out

        file_content_b64 = base64.b64encode(content).decode("utf-8")
        upload = whatsapp_hub_client.upload_media(
            account_name, file_content_b64, mime, filename)
        if upload.get("success"):
            out["media_id"] = upload.get("media_id")
            out["ok"] = bool(out["media_id"])
            if not out["ok"]:
                out["error"] = _("Hub upload returned no media_id")
        else:
            out["error"] = upload.get("error") or _("Media upload failed")
    except Exception as e:
        out["error"] = str(e)
        frappe.log_error(frappe.get_traceback(), "_upload_header failed")
    return out


def _resolve_header_bytes(header_source):
    """Return (content_bytes, mime, filename) for the media described by
    `header_source`, reading the File's bytes locally (works for private files
    that Meta could not fetch by URL). Supports an explicit local File
    (`file_url`); a rendered print PDF (`print_format` + `doctype` + `name`); or
    the first File attached to (`attached_to_doctype`, `attached_to_name`).
    Returns (None, None, None) when nothing resolves."""
    file_url = header_source.get("file_url")
    if file_url:
        file_doc = frappe.get_doc("File", {"file_url": file_url})
        return file_doc.get_content(), _mime_for(file_doc.file_name), file_doc.file_name

    print_format = header_source.get("print_format")
    if print_format:
        doctype = header_source.get("doctype")
        name = header_source.get("name")
        pdf = frappe.get_print(doctype, name, print_format=print_format, as_pdf=True)
        return pdf, "application/pdf", "{0}.pdf".format(name)

    attached_to_doctype = header_source.get("attached_to_doctype")
    attached_to_name = header_source.get("attached_to_name")
    if attached_to_doctype and attached_to_name:
        file_name = frappe.db.get_value("File", {
            "attached_to_doctype": attached_to_doctype,
            "attached_to_name": attached_to_name,
        }, "name")
        if file_name:
            file_doc = frappe.get_doc("File", file_name)
            return file_doc.get_content(), _mime_for(file_doc.file_name), file_doc.file_name

    return None, None, None


def _raw_size_cap(header_format):
    """Effective raw-byte cap = min(Meta's per-format cap, the hub's request-body
    cap / ~1.34x base64 inflation). The hub cap usually wins for documents."""
    meta_cap = _META_MEDIA_CAP[header_format]
    hub_raw_cap = int(_get_hub_max_content_length() / _BASE64_INFLATION)
    return min(meta_cap, hub_raw_cap)


def _get_hub_max_content_length():
    """Best-effort read of YRP WhatsApp Hub Settings.hub_max_content_length;
    falls back to the hub's 25 MB default when the field/DocType is absent."""
    try:
        settings = frappe.get_single("YRP WhatsApp Hub Settings")
        val = settings.get("hub_max_content_length")
        if val:
            return int(val)
    except Exception:
        pass
    return _HUB_DEFAULT_MAX_CONTENT_LENGTH


def _ordered_values(params):
    """Values sorted by positional int key ascending. A dict keyed by positional
    strings ("1".."10") must sort by int(key) — string sort would place "10"
    before "2". A list/tuple is returned as-is; None yields []."""
    if params is None:
        return []
    if isinstance(params, (list, tuple)):
        return list(params)
    return [params[k] for k in sorted(params, key=lambda k: int(k))]


def _as_text(value):
    return "" if value is None else str(value)


def _mime_for(filename):
    guessed, _enc = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"
