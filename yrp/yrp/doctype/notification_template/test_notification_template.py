# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Task 09 — Notification Template WhatsApp channel, Supplier recipient map,
and the Communication `communication_medium` Property Setter.

Reuses the SMS neighbour's fixtures (`_NotificationTestBase` rolls back per
CLASS in v16's compat harness, and clears templates each test)."""

from unittest.mock import patch

import frappe

from yrp.yrp.doctype.notification_template.notification_template import (
    NotificationTemplate,
    add_whatsapp_communication_medium,
)
from yrp.yrp.doctype.supplier.test_supplier_notification import (
    _NotificationTestBase,
    _contact_for,
    _notification_template,
    _purchase_order,
    _supplier,
    _warehouse,
)

# send_whatsapp imports `deliver_whatsapp_template` from this module at call
# time, so patching the source attribute intercepts the hub round-trip.
DELIVER = "yrp.whatsapp.deliver_whatsapp_template"
# _log_whatsapp_communication imports `_make` locally; patch the source.
MAKE_COMM = "frappe.core.doctype.communication.email._make"


class TestNotificationTemplateWhatsApp(_NotificationTestBase):
    def _wa_po(self, mobile="98765 43210"):
        supplier = _supplier(f"_T WA Supplier {frappe.generate_hash(length=6)}")
        _contact_for(supplier, mobile=mobile)
        return _purchase_order(qty=1, warehouse=_warehouse("_T WA WH"), supplier=supplier)

    # --- channel dispatch --------------------------------------------------

    def test_channel_whatsapp_routes_to_send_whatsapp(self):
        tmpl = _notification_template(
            f"_T PO WA {frappe.generate_hash(length=6)}", channel="WhatsApp"
        )
        with patch.object(NotificationTemplate, "send_whatsapp") as mock_wa:
            tmpl.send("SOME-PO", "Submit", ["919000000000"])
        mock_wa.assert_called_once_with("SOME-PO", ["919000000000"])

    def test_channel_mismatch_does_not_route(self):
        tmpl = _notification_template(
            f"_T PO WA {frappe.generate_hash(length=6)}", channel="WhatsApp", event="Submit"
        )
        with patch.object(NotificationTemplate, "send_whatsapp") as mock_wa:
            tmpl.send("SOME-PO", "Cancel", ["919000000000"])  # event != Submit
        mock_wa.assert_not_called()

    # --- send_whatsapp resolves the template & calls deliver ---------------

    def test_send_whatsapp_calls_deliver_with_resolved_template(self):
        po = self._wa_po()
        tmpl = _notification_template(
            f"_T PO WA {frappe.generate_hash(length=6)}", channel="WhatsApp",
            body="PO {{ doc.name }}",
        )
        with patch.object(
            NotificationTemplate, "_resolve_whatsapp_template",
            return_value=("essdee_wa", "order_confirmation", "en"),
        ), patch(DELIVER, return_value={"ok": True, "meta_message_id": "wamid.T"}) as mock_deliver, \
             patch(MAKE_COMM) as mock_make:
            tmpl.send_whatsapp(po.name, ["919000000000"])
        mock_deliver.assert_called_once()
        kw = mock_deliver.call_args.kwargs
        self.assertEqual(kw["to_number"], "919000000000")
        self.assertEqual(kw["template_name"], "order_confirmation")
        self.assertEqual(kw["language_code"], "en")
        self.assertEqual(kw["account_name"], "essdee_wa")
        mock_make.assert_called_once()  # best-effort timeline Communication attempted

    def test_send_whatsapp_msgprints_when_unconfigured(self):
        po = self._wa_po()
        tmpl = _notification_template(
            f"_T PO WA {frappe.generate_hash(length=6)}", channel="WhatsApp",
        )
        with patch.object(NotificationTemplate, "_resolve_whatsapp_template", return_value=None), \
             patch(DELIVER) as mock_deliver:
            tmpl.send_whatsapp(po.name, ["919000000000"])  # no throw
        mock_deliver.assert_not_called()

    # --- Communication failure is isolated (savepoint), never rolls back ---

    def test_communication_failure_is_isolated(self):
        po = self._wa_po()
        tmpl = _notification_template(
            f"_T PO WA Iso {frappe.generate_hash(length=6)}", channel="WhatsApp",
            body="PO {{ doc.name }}",
        )
        # A prior write inside the same transaction that MUST survive.
        sentinel = frappe.get_doc(
            {"doctype": "ToDo", "description": "wa-iso-sentinel"}
        ).insert(ignore_permissions=True)
        with patch.object(
            NotificationTemplate, "_resolve_whatsapp_template",
            return_value=("essdee_wa", "order_confirmation", "en"),
        ), patch(DELIVER, return_value={"ok": True, "meta_message_id": "wamid.X"}) as mock_deliver, \
             patch(MAKE_COMM, side_effect=frappe.ValidationError("communication_medium invalid")):
            # send_whatsapp must NOT raise even though _make blows up.
            tmpl.send_whatsapp(po.name, ["919000000000"])
        mock_deliver.assert_called_once()
        # The savepoint rollback undid only the failed Communication.
        self.assertTrue(frappe.db.exists("ToDo", sentinel.name))
        self.assertFalse(frappe.db.exists("Communication", {
            "reference_doctype": "Purchase Order",
            "reference_name": po.name,
            "communication_medium": "WhatsApp",
        }))

    # --- the Property Setter makes medium "WhatsApp" a valid option --------

    def test_property_setter_makes_whatsapp_medium_valid(self):
        from frappe.core.doctype.communication.email import _make as make_communication

        po = self._wa_po()
        add_whatsapp_communication_medium()
        options = frappe.get_meta("Communication").get_field("communication_medium").options
        self.assertIn("WhatsApp", (options or "").split("\n"))
        # _make with the new medium now passes _validate_selects and inserts.
        make_communication(
            doctype="Purchase Order", name=po.name, content="hi", subject="WhatsApp",
            sender="", recipients=["919000000000"], communication_medium="WhatsApp",
            send_email=False, communication_type="Automated Message",
        )
        self.assertTrue(frappe.db.exists("Communication", {
            "reference_doctype": "Purchase Order",
            "reference_name": po.name,
            "communication_medium": "WhatsApp",
        }))

    # --- Supplier.send_notification maps WhatsApp -> mobile ----------------

    def test_supplier_send_notification_routes_whatsapp_to_mobile(self):
        supplier = _supplier(f"_T WA Sup {frappe.generate_hash(length=6)}")
        _contact_for(supplier, mobile="98765 43210")
        po = _purchase_order(qty=1, warehouse=_warehouse("_T WA WH"), supplier=supplier)
        _notification_template(f"_T PO WA {frappe.generate_hash(length=6)}", channel="WhatsApp")
        with patch.object(NotificationTemplate, "send_whatsapp") as mock_wa:
            frappe.get_doc("Supplier", supplier).send_notification(
                "Purchase Order", po.name, ["WhatsApp"], "Submit"
            )
        mock_wa.assert_called_once()
        self.assertEqual(mock_wa.call_args.args[1], ["98765 43210"])
