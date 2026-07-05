# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.utils import now_datetime

# Monotonic status ladder. A WhatsApp Notification Log only ever moves forward
# along this axis; a positive status also supersedes a prior Failed (rank -1).
# Failed itself is OFF this axis (see process_status_update).
_STATUS_RANK = {"Queued": 0, "Sent": 1, "Delivered": 2, "Read": 3}
_STATUS_STAMP = {"Sent": "sent_at", "Delivered": "delivered_at", "Read": "read_at"}

# Meta template review-status push -> local YRP WhatsApp Template.status
# (byte-for-byte from the reference webhook._apply_template_status_update).
_TEMPLATE_STATUS_MAP = {
    "PENDING": "IN_REVIEW",
    "FLAGGED": "REJECTED",
    "DRAFT": "DRAFT",
    "IN_REVIEW": "IN_REVIEW",
    "APPROVED": "APPROVED",
    "REJECTED": "REJECTED",
    "PAUSED": "PAUSED",
    "DISABLED": "DISABLED",
}


def process_payload(payload, webhook_log=None):
    """Route a hub-forwarded WhatsApp webhook payload (status-only spoke).

    Two shapes are accepted:

    * ``{"template_status_update": {...}}`` -- a Meta template review-status
      push; refresh the local ``YRP WhatsApp Template`` mirror via
      ``_apply_template_status_update`` (sets ``flags.from_meta_sync``).
    * a standard Meta webhook envelope -- iterate ``entry[].changes[].value``
      and for every ``statuses[]`` entry advance the matching
      ``WhatsApp Notification Log`` (delivery truth). ``messages[]`` (customer
      replies) are COUNTED ONLY -- the seam a future inbox would hook into; v1
      is status-only.

    Never raises to the endpoint: any failure is captured on the webhook log
    (``processed=0`` + traceback) and re-surfaced only into the Error Log, so
    the caller can still answer the hub ``{"ok": True}`` and avoid a retry
    storm.
    """
    event_type = "other"
    statuses_applied = 0
    inbound_messages = 0
    try:
        if isinstance(payload, dict) and "template_status_update" in payload:
            _apply_template_status_update(payload["template_status_update"])
            event_type = "template_status_update"
        else:
            entries = payload.get("entry", []) if isinstance(payload, dict) else []
            saw_statuses = False
            for entry in entries:
                for change in entry.get("changes", []):
                    value = change.get("value", {}) or {}
                    statuses = value.get("statuses", []) or []
                    if statuses:
                        saw_statuses = True
                    for status in statuses:
                        if process_status_update(status):
                            statuses_applied += 1
                    for _message in value.get("messages", []) or []:
                        # Status-only spoke: a customer reply is the
                        # future-inbox seam. Count it for observability, then
                        # ignore it.
                        inbound_messages += 1
            if saw_statuses:
                event_type = "status_update"
            elif inbound_messages:
                event_type = "inbound_message"

        _stamp_webhook_log(
            webhook_log,
            processed=True,
            event_type=event_type,
            statuses_applied=statuses_applied,
            inbound_messages=inbound_messages,
        )
        return {
            "event_type": event_type,
            "statuses_applied": statuses_applied,
            "inbound_messages": inbound_messages,
        }
    except Exception:
        frappe.log_error(
            "WhatsApp Webhook Processing Error", frappe.get_traceback()
        )
        _stamp_webhook_log(
            webhook_log,
            processed=False,
            event_type=event_type,
            statuses_applied=statuses_applied,
            inbound_messages=inbound_messages,
            error=frappe.get_traceback(),
        )
        return {
            "event_type": event_type,
            "statuses_applied": statuses_applied,
            "inbound_messages": inbound_messages,
            "error": True,
        }


def process_status_update(status_data):
    """Advance a WhatsApp Notification Log by meta_message_id from one status.

    Monotonic (the guard the reference lacked): ``Queued(0) < Sent(1) <
    Delivered(2) < Read(3)``. A positive status only ever moves the row forward
    and supersedes a prior ``Failed`` (rank -1). ``failed`` is off the rank
    axis -- it applies only when the row has not already reached a positive
    terminal (``Delivered`` / ``Read``), so a late or duplicate ``failed`` can
    never clobber a real delivery / read.

    Runs inside a per-status ``savepoint`` with a ``FOR UPDATE`` row lock
    (added over the reference) so concurrent webhook deliveries for the same
    ``meta_message_id`` serialise and cannot race a regression. Unknown status,
    unknown ``meta_message_id``, or a lower/equal rank are all silent no-ops.
    Returns ``True`` only when the row was actually advanced.
    """
    meta_message_id = status_data.get("id")
    if not meta_message_id:
        return False

    # Meta sends lowercase ("sent"/"delivered"/"read"/"failed"); .capitalize()
    # maps them onto the local Select options.
    status = (status_data.get("status") or "").capitalize()
    if status not in _STATUS_RANK and status != "Failed":
        # A status we do not track (e.g. "Deleted") -- ignore.
        return False

    save_point = "wa_status_update"
    frappe.db.savepoint(save_point)
    try:
        row = frappe.db.get_value(
            "WhatsApp Notification Log",
            {"meta_message_id": meta_message_id},
            ["name", "status"],
            for_update=True,  # ROW LOCK -- serialise concurrent status writes
            as_dict=True,
        )
        update = None
        if row:
            current = row.status or "Queued"
            if status == "Failed":
                # Off-axis: never overwrite a positive terminal.
                if current not in ("Delivered", "Read"):
                    update = {
                        "status": "Failed",
                        "error": json.dumps(status_data.get("errors", [])),
                    }
            else:
                # A prior Failed is superseded by any positive status (rank -1).
                current_rank = (
                    -1 if current == "Failed" else _STATUS_RANK.get(current, 0)
                )
                if _STATUS_RANK[status] > current_rank:
                    update = {
                        "status": status,
                        _STATUS_STAMP[status]: now_datetime(),
                    }
        if update:
            frappe.db.set_value(
                "WhatsApp Notification Log", row.name, update
            )
        frappe.db.release_savepoint(save_point)
        return bool(update)
    except Exception:
        frappe.db.rollback(save_point=save_point)
        frappe.log_error(
            "WhatsApp Status Update Error", frappe.get_traceback()
        )
        return False


def _apply_template_status_update(payload):
    """Refresh a local YRP WhatsApp Template from a hub-forwarded Meta push.

    Replicates the reference ``webhook._apply_template_status_update``: match
    by the Meta template name (raw or lower+underscore normalised), map the
    ``event`` onto the local status ladder, and save with
    ``flags.from_meta_sync`` set so the template controller neither re-forces
    DRAFT nor tries to push the mirror back to Meta.
    """
    if not isinstance(payload, dict):
        return
    template_name = payload.get("message_template_name")
    if not template_name:
        return
    normalized = template_name.lower().replace(" ", "_")
    candidates = frappe.get_all(
        "YRP WhatsApp Template",
        filters=[["template_name", "in", [template_name, normalized]]],
        fields=["name"],
        limit=1,
    )
    if not candidates:
        return
    doc = frappe.get_doc("YRP WhatsApp Template", candidates[0].name)
    event = (payload.get("event") or "").upper()
    mapped = _TEMPLATE_STATUS_MAP.get(event)
    if mapped:
        doc.status = mapped
    reason = payload.get("reason")
    if reason and hasattr(doc, "reason"):
        doc.reason = reason
    # Load-bearing: from_meta_sync tells the template controller this write is a
    # mirror refresh (do not force DRAFT, do not push back to Meta).
    doc.flags.from_meta_sync = True
    doc.save(ignore_permissions=True)


def _stamp_webhook_log(
    webhook_log,
    *,
    processed,
    event_type,
    statuses_applied,
    inbound_messages,
    error=None,
):
    """Persist the observability counters + processed flag onto the raw webhook
    log. No-op when called without a log (e.g. a direct unit-test call to
    ``process_status_update``). Uses ``frappe.db.set_value`` (not ``doc.save``)
    so the audit stamp is a cheap direct write that never re-runs webhook-log
    validation, and mirrors the in-memory doc so the caller sees fresh values.
    """
    if not webhook_log:
        return
    fields = {
        "processed": 1 if processed else 0,
        "event_type": event_type,
        "statuses_applied": statuses_applied,
        "inbound_messages": inbound_messages,
    }
    if error:
        fields["error"] = error
    frappe.db.set_value(
        "YRP WhatsApp Webhook Log", webhook_log.name, fields
    )
    for key, value in fields.items():
        setattr(webhook_log, key, value)
