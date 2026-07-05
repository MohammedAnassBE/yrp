# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp import whatsapp_notification
from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
    _purchase_order,
    _supplier,
    _warehouse,
)
from yrp.yrp.doctype.supplier.test_supplier_notification import _contact_for

ACCOUNT = "yrp-wa-notif-acct"
# Patch target: deliver is imported inside the endpoint at call time, so we
# patch it at its source module (yrp.whatsapp). Mocking it keeps every test
# off the real hub while still exercising the full split/build/log path.
DELIVER = "yrp.whatsapp.deliver_whatsapp_template"


class TestWhatsAppNotification(IntegrationTestCase):
    # ---- fixtures -----------------------------------------------------------
    def setUp(self):
        # get_whatsapp_context scans ALL APPROVED templates (then filters by
        # applicable_doctypes in Python) -- IntegrationTestCase only rolls
        # back at class teardown, not per test method, so an APPROVED
        # "Purchase Order"-applicable mirror left behind by an earlier test
        # method in this class would otherwise leak into a later test's scan.
        # Wipe this class's own fixtures before every test for isolation.
        for name in frappe.get_all("YRP WhatsApp Template",
                filters={"template_name": ["like", "yrp_wa_%"]}, pluck="name"):
            frappe.delete_doc("YRP WhatsApp Template", name,
                ignore_permissions=True, force=True)

    def _ensure_account(self):
        if not frappe.db.exists("YRP WhatsApp Account", ACCOUNT):
            frappe.get_doc({
                "doctype": "YRP WhatsApp Account",
                "account_name": ACCOUNT,
                "is_default": 1,
                "enabled": 1,
            }).insert(ignore_permissions=True)
        return ACCOUNT

    def _ensure_mirror(self, template_name, status="APPROVED",
            header_type=None, header_content=None,
            body_text="Order {{1}} for {{2}}",
            applicable_doctypes=None, sample_values=None):
        fq = f"{template_name}-en"
        if frappe.db.exists("YRP WhatsApp Template", fq):
            frappe.delete_doc("YRP WhatsApp Template", fq,
                ignore_permissions=True, force=True)
        doc = frappe.get_doc({
            "doctype": "YRP WhatsApp Template",
            "template_name": template_name,
            "language_code": "en",
            "category": "UTILITY",
            "status": status,
            "whatsapp_account": self._ensure_account(),
            "header_type": header_type,
            "header_content": header_content,
            "body_text": body_text,
        })
        for row in (applicable_doctypes or []):
            doc.append("applicable_doctypes", {"reference_doctype": row})
        for row in (sample_values or []):
            doc.append("sample_values", row)
        doc.flags.from_meta_sync = True   # preserve the given status (Task 02 guard)
        doc.insert(ignore_permissions=True)
        return doc

    def _configure_enabled_doctypes(self, rows):
        """rows: list of (reference_doctype, supplier_key, enabled) tuples."""
        settings = frappe.get_doc("YRP WhatsApp Hub Settings")
        settings.enabled = 1
        settings.hub_url = "http://127.0.0.1:8899"
        settings.api_key = "test-key"
        settings.api_secret = "test-secret"
        settings.set("accounts", [])
        settings.append("accounts", {"account_name": self._ensure_account(), "is_default": 1})
        settings.set("enabled_doctypes", [])
        for reference_doctype, supplier_key, enabled in rows:
            settings.append("enabled_doctypes", {
                "reference_doctype": reference_doctype,
                "supplier_key": supplier_key,
                "enabled": enabled,
            })
        settings.save(ignore_permissions=True)

    def _ensure_file(self, file_name, content=b"file-bytes", is_private=1,
            attached_to_doctype=None, attached_to_name=None):
        """A real File row (content on disk + content_hash) so header_file
        resolution in send_whatsapp_notification (frappe.get_doc("File",
        {"file_url": ...})) finds an actual record instead of throwing
        DoesNotExistError on a made-up path."""
        return frappe.get_doc({
            "doctype": "File",
            "file_name": file_name,
            "content": content,
            "is_private": is_private,
            "attached_to_doctype": attached_to_doctype,
            "attached_to_name": attached_to_name,
        }).insert(ignore_permissions=True)

    def _ensure_purchase_manager_user(self, email="yrp-wa-purchase-mgr@example.com"):
        """A non-Administrator user who can write Purchase Order (so the
        top-of-function frappe.has_permission write gate passes) but has no
        special relationship to any given File -- used to prove the header_file
        read-permission gate is independent of doc-write permission."""
        if not frappe.db.exists("User", email):
            frappe.get_doc({
                "doctype": "User",
                "email": email,
                "first_name": "WA Purchase Mgr",
                "send_welcome_email": 0,
            }).insert(ignore_permissions=True)
        frappe.get_doc("User", email).add_roles("Purchase Manager")
        return email

    def _submitted_po(self, mobile="98765 43210"):
        supplier = _supplier(f"_T WA Supplier {frappe.generate_hash(length=6)}")
        _contact_for(supplier, mobile=mobile)
        return _purchase_order(qty=1, warehouse=_warehouse("_T WA WH"), supplier=supplier)

    def _ok(self, meta_id="wamid.OK"):
        return {"ok": True, "meta_message_id": meta_id, "http_status": 200,
            "raw": "{}", "error": None, "meta_error": None,
            "media_id": None, "media_mime": None, "file_name": None}

    def _failed(self):
        return {"ok": False, "meta_message_id": None, "http_status": 400,
            "raw": '{"success": false}', "error": "Meta rejected",
            "meta_error": '{"code": 131000}',
            "media_id": None, "media_mime": None, "file_name": None}

    # ---- get_whatsapp_context: is_doctype_enabled gating ---------------------
    def test_context_throws_when_doctype_not_enabled(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([])  # Purchase Order not listed at all
        with self.assertRaises(frappe.ValidationError) as ctx:
            whatsapp_notification.get_whatsapp_context("Purchase Order", po.name)
        self.assertIn("not enabled", str(ctx.exception))

    def test_context_throws_when_doctype_row_disabled(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 0)])
        with self.assertRaises(frappe.ValidationError):
            whatsapp_notification.get_whatsapp_context("Purchase Order", po.name)

    # ---- get_whatsapp_context: template filtering + variable resolution -----
    def test_context_returns_approved_applicable_templates_with_resolved_vars(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_ctx", status="APPROVED",
            body_text="Order {{1}} for {{2}}",
            applicable_doctypes=["Purchase Order"],
            # {{1}} -> doc.name (plain field), {{2}} -> literal
            sample_values=[
                {"variable_number": 1, "variable_type": "body", "sample_value": "name"},
                {"variable_number": 2, "variable_type": "body", "sample_value": "=Ready for dispatch"},
            ])

        ctx = whatsapp_notification.get_whatsapp_context("Purchase Order", po.name)
        self.assertEqual(ctx["supplier"], po.supplier)
        self.assertEqual(ctx["mobile"], "98765 43210")
        self.assertEqual(len(ctx["templates"]), 1)
        t = ctx["templates"][0]
        self.assertEqual(t["template_name"], "yrp_wa_ctx")
        self.assertEqual(t["language_code"], "en")
        self.assertEqual(t["body_text"], "Order {{1}} for {{2}}")
        self.assertFalse(t["needs_media"])
        by_name = {v["name"]: v for v in t["variables"]}
        self.assertEqual(by_name["{{1}}"]["type"], "body")
        self.assertEqual(by_name["{{1}}"]["value"], po.name)              # field resolve
        self.assertEqual(by_name["{{2}}"]["value"], "Ready for dispatch")  # literal resolve
        self.assertIn("doc_fields", ctx)
        self.assertIsInstance(ctx["doc_fields"], list)

    def test_context_unresolved_variable_is_blank(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_blank", status="APPROVED",
            body_text="Hi {{1}}", applicable_doctypes=["Purchase Order"])  # no sample_values
        ctx = whatsapp_notification.get_whatsapp_context("Purchase Order", po.name)
        self.assertEqual(ctx["templates"][0]["variables"][0]["value"], "")

    def test_context_resolves_dotted_link_path(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_dot", status="APPROVED", body_text="Hi {{1}}",
            applicable_doctypes=["Purchase Order"],
            sample_values=[
                {"variable_number": 1, "variable_type": "body", "sample_value": "supplier.supplier_name"},
            ])
        ctx = whatsapp_notification.get_whatsapp_context("Purchase Order", po.name)
        expected = frappe.db.get_value("Supplier", po.supplier, "supplier_name")
        self.assertEqual(ctx["templates"][0]["variables"][0]["value"], expected)

    def test_context_collects_header_and_body_variables(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_hdr", status="APPROVED",
            header_type="TEXT", header_content="Update {{1}}",
            body_text="Order {{1}} for {{2}}",
            applicable_doctypes=["Purchase Order"])
        ctx = whatsapp_notification.get_whatsapp_context("Purchase Order", po.name)
        t = ctx["templates"][0]
        types_by_name = {(v["name"], v["type"]) for v in t["variables"]}
        self.assertIn(("{{1}}", "body"), types_by_name)
        self.assertIn(("{{2}}", "body"), types_by_name)
        self.assertIn(("{{1}}", "header"), types_by_name)

    def test_context_needs_media_true_for_media_header(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_media", status="APPROVED",
            header_type="IMAGE", body_text="Order {{1}} ready",
            applicable_doctypes=["Purchase Order"])
        ctx = whatsapp_notification.get_whatsapp_context("Purchase Order", po.name)
        self.assertTrue(ctx["templates"][0]["needs_media"])
        # an IMAGE header carries no positional header variables
        self.assertFalse(any(v["type"] == "header" for v in ctx["templates"][0]["variables"]))

    def test_context_without_supplier_throws(self):
        wh = _warehouse(f"_T WA NoSup {frappe.generate_hash(length=4)}")
        self._configure_enabled_doctypes([("Warehouse", "supplier", 1)])
        with self.assertRaises(frappe.ValidationError):
            whatsapp_notification.get_whatsapp_context("Warehouse", wh)

    def test_context_without_mobile_throws(self):
        po = self._submitted_po(mobile=None)
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_nomob", status="APPROVED",
            applicable_doctypes=["Purchase Order"])
        with self.assertRaises(frappe.ValidationError):
            whatsapp_notification.get_whatsapp_context("Purchase Order", po.name)

    def test_context_filters_non_approved_template(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_draft", status="DRAFT",
            applicable_doctypes=["Purchase Order"])
        # a configured-but-not-APPROVED mirror is filtered out -> no sendable template
        with self.assertRaises(frappe.ValidationError):
            whatsapp_notification.get_whatsapp_context("Purchase Order", po.name)

    def test_context_filters_template_not_applicable_to_doctype(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        # APPROVED, but its applicable_doctypes doesn't include Purchase Order
        self._ensure_mirror("yrp_wa_notapplic", status="APPROVED",
            applicable_doctypes=["Stock Entry"])
        with self.assertRaises(frappe.ValidationError):
            whatsapp_notification.get_whatsapp_context("Purchase Order", po.name)

    # ---- send_whatsapp_notification -----------------------------------------
    def test_send_writes_failed_log_and_red_msgprint_no_throw(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_send", status="APPROVED",
            applicable_doctypes=["Purchase Order"])

        with patch(DELIVER, return_value=self._failed()):
            with patch("frappe.msgprint") as mock_msg:
                # NO exception even though delivery failed
                result = whatsapp_notification.send_whatsapp_notification(
                    "Purchase Order", po.name, template_name="yrp_wa_send",
                    language_code="en",
                    params={"body:1": po.name, "body:2": po.supplier},
                    mobile_no="919000000009")
        self.assertFalse(result["ok"])
        self.assertTrue(mock_msg.called)
        self.assertEqual(mock_msg.call_args.kwargs.get("indicator"), "red")

        log = frappe.get_last_doc("WhatsApp Notification Log",
            filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
        self.assertEqual(log.status, "Failed")
        self.assertEqual(log.mobile_no, "919000000009")
        self.assertEqual(log.error, "Meta rejected")
        self.assertIsNone(log.sent_at)
        self.assertIn("body_vars", log.message_variables)

    def test_send_happy_writes_sent_log_and_returns_result(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_send", status="APPROVED",
            body_text="Order {{1}} for {{2}}",
            applicable_doctypes=["Purchase Order"])

        with patch(DELIVER, return_value=self._ok("wamid.HAPPY")) as mock_deliver:
            result = whatsapp_notification.send_whatsapp_notification(
                "Purchase Order", po.name, template_name="yrp_wa_send",
                language_code="en",
                params={"body:1": po.name, "body:2": po.supplier},
                mobile_no="919000000009")

        # the result dict is returned (so a /web modal can gate on result.ok)
        self.assertTrue(result["ok"])
        self.assertEqual(result["meta_message_id"], "wamid.HAPPY")

        kwargs = mock_deliver.call_args.kwargs
        self.assertEqual(kwargs["template_name"], "yrp_wa_send")
        self.assertEqual(kwargs["language_code"], "en")     # keyed on given language
        self.assertEqual(kwargs["to_number"], "919000000009")
        self.assertEqual(kwargs["body_vars"], [po.name, po.supplier])  # split + ordered
        self.assertIsNone(kwargs["header_source"])          # no header_file given

        log = frappe.get_last_doc("WhatsApp Notification Log",
            filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
        self.assertEqual(log.status, "Sent")
        self.assertEqual(log.meta_message_id, "wamid.HAPPY")
        self.assertEqual(log.template_name, "yrp_wa_send")
        self.assertEqual(log.message, f"Order {po.name} for {po.supplier}")  # rendered preview
        self.assertTrue(log.sent_at)

    def test_send_builds_header_source_from_header_file_for_media_template(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_media_send", status="APPROVED",
            header_type="IMAGE", body_text="Order {{1}} photo attached",
            applicable_doctypes=["Purchase Order"])
        photo = self._ensure_file("photo.bin",
            attached_to_doctype="Purchase Order", attached_to_name=po.name)

        with patch(DELIVER, return_value=self._ok("wamid.MEDIA")) as mock_deliver:
            result = whatsapp_notification.send_whatsapp_notification(
                "Purchase Order", po.name, template_name="yrp_wa_media_send",
                language_code="en", params={"body:1": po.name},
                mobile_no="919000000009",
                header_file=photo.file_url)

        self.assertTrue(result["ok"])
        kwargs = mock_deliver.call_args.kwargs
        self.assertEqual(kwargs["header_source"], {
            "header_format": "IMAGE", "file_url": photo.file_url,
        })

        log = frappe.get_last_doc("WhatsApp Notification Log",
            filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
        self.assertEqual(log.message_type, "Media")

    def test_send_ignores_header_file_for_text_header_template(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_text_send", status="APPROVED",
            header_type="TEXT", header_content="Hello", body_text="Order {{1}}",
            applicable_doctypes=["Purchase Order"])
        attachment = self._ensure_file("ignored.bin",
            attached_to_doctype="Purchase Order", attached_to_name=po.name)

        with patch(DELIVER, return_value=self._ok("wamid.TXT")) as mock_deliver:
            whatsapp_notification.send_whatsapp_notification(
                "Purchase Order", po.name, template_name="yrp_wa_text_send",
                language_code="en", params={"body:1": po.name},
                mobile_no="919000000009",
                header_file=attachment.file_url)

        kwargs = mock_deliver.call_args.kwargs
        self.assertIsNone(kwargs["header_source"])

    # ---- send_whatsapp_notification: governance gate (mirrors get_whatsapp_context) ----
    def test_send_throws_when_doctype_not_enabled(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([])  # Purchase Order not listed at all
        self._ensure_mirror("yrp_wa_gate1", status="APPROVED",
            applicable_doctypes=["Purchase Order"])

        with patch(DELIVER) as mock_deliver:
            with self.assertRaises(frappe.ValidationError) as ctx:
                whatsapp_notification.send_whatsapp_notification(
                    "Purchase Order", po.name, template_name="yrp_wa_gate1",
                    language_code="en",
                    params={"body:1": po.name, "body:2": po.supplier},
                    mobile_no="919000000009")
        self.assertIn("not enabled", str(ctx.exception))
        mock_deliver.assert_not_called()
        self.assertFalse(frappe.db.exists("WhatsApp Notification Log",
            {"reference_doctype": "Purchase Order", "reference_name": po.name}))

    def test_send_throws_when_template_not_applicable(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        # APPROVED, but its applicable_doctypes doesn't include Purchase Order --
        # write permission on the PO alone must not be enough to fire this template.
        self._ensure_mirror("yrp_wa_gate2", status="APPROVED",
            applicable_doctypes=["Stock Entry"])

        with patch(DELIVER) as mock_deliver:
            with self.assertRaises(frappe.ValidationError) as ctx:
                whatsapp_notification.send_whatsapp_notification(
                    "Purchase Order", po.name, template_name="yrp_wa_gate2",
                    language_code="en",
                    params={"body:1": po.name, "body:2": po.supplier},
                    mobile_no="919000000009")
        self.assertIn("not applicable", str(ctx.exception))
        mock_deliver.assert_not_called()

    def test_send_throws_permission_error_for_unreadable_header_file(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_gate3", status="APPROVED",
            header_type="IMAGE", body_text="Order {{1}} photo",
            applicable_doctypes=["Purchase Order"])
        # private, unattached, unshared, owned by Administrator (the inserting
        # user) -- only Administrator / the owner / an explicit share may
        # read it, so a plain write-permitted Purchase Manager must not.
        secret = self._ensure_file("secret.bin", is_private=1)

        limited_user = self._ensure_purchase_manager_user()
        frappe.set_user(limited_user)
        try:
            with patch(DELIVER) as mock_deliver:
                with self.assertRaises(frappe.PermissionError):
                    whatsapp_notification.send_whatsapp_notification(
                        "Purchase Order", po.name, template_name="yrp_wa_gate3",
                        language_code="en", params={"body:1": po.name},
                        mobile_no="919000000009",
                        header_file=secret.file_url)
            mock_deliver.assert_not_called()
        finally:
            frappe.set_user("Administrator")

    def test_send_happy_path_with_full_governance_passes(self):
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        self._ensure_mirror("yrp_wa_gate4", status="APPROVED",
            header_type="IMAGE", body_text="Order {{1}} ready",
            applicable_doctypes=["Purchase Order"])
        photo = self._ensure_file("ready.bin",
            attached_to_doctype="Purchase Order", attached_to_name=po.name)

        with patch(DELIVER, return_value=self._ok("wamid.GATE4")) as mock_deliver:
            result = whatsapp_notification.send_whatsapp_notification(
                "Purchase Order", po.name, template_name="yrp_wa_gate4",
                language_code="en", params={"body:1": po.name},
                mobile_no="919000000009", header_file=photo.file_url)

        self.assertTrue(result["ok"])
        mock_deliver.assert_called_once()
        log = frappe.get_last_doc("WhatsApp Notification Log",
            filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
        self.assertEqual(log.status, "Sent")
        self.assertEqual(log.message_type, "Media")

    # ---- _build_media_header_source ------------------------------------------
    def test_build_media_header_source_for_image(self):
        hs = whatsapp_notification._build_media_header_source("IMAGE", "/private/files/photo.jpg")
        self.assertEqual(hs, {"header_format": "IMAGE", "file_url": "/private/files/photo.jpg"})

    def test_build_media_header_source_returns_none_for_text_header(self):
        self.assertIsNone(whatsapp_notification._build_media_header_source("TEXT", "/private/files/x.jpg"))

    def test_build_media_header_source_returns_none_without_file(self):
        self.assertIsNone(whatsapp_notification._build_media_header_source("IMAGE", None))

    # ---- get_enabled_whatsapp_doctypes ----------------------------------------
    def test_get_enabled_whatsapp_doctypes_returns_map(self):
        self._configure_enabled_doctypes([
            ("Purchase Order", "supplier", 1),
            ("Stock Entry", "to_supplier", 1),
            ("Delivery Challan", "supplier", 0),
        ])
        result = whatsapp_notification.get_enabled_whatsapp_doctypes()
        self.assertEqual(result["doctypes"].get("Purchase Order"), "supplier")
        self.assertEqual(result["doctypes"].get("Stock Entry"), "to_supplier")
        self.assertNotIn("Delivery Challan", result["doctypes"])

    # ---- resend_whatsapp_notification_log -----------------------------------
    def test_resend_blocks_when_reference_deleted(self):
        from yrp.yrp.doctype.whatsapp_notification_log.whatsapp_notification_log import (
            create_whatsapp_log,
        )
        po = self._submitted_po()
        log = create_whatsapp_log(
            reference_doctype="Purchase Order", reference_name=po.name,
            supplier=po.supplier, contact=None, mobile_no="919000000009",
            result=self._ok(), template_name="yrp_wa_send", language_code="en",
            message_variables={"header_vars": [], "body_vars": [po.name, po.supplier]})
        # simulate the reference doc being gone (DB-level, bypasses link checks)
        frappe.db.set_value("WhatsApp Notification Log", log.name,
            "reference_name", "NONEXISTENT-PO-XYZ")

        with patch(DELIVER) as mock_deliver:
            with self.assertRaises(frappe.ValidationError):
                whatsapp_notification.resend_whatsapp_notification_log(log.name)
        mock_deliver.assert_not_called()   # blocked before any send

    def test_resend_throws_when_doctype_not_enabled(self):
        from yrp.yrp.doctype.whatsapp_notification_log.whatsapp_notification_log import (
            create_whatsapp_log,
        )
        po = self._submitted_po()
        self._configure_enabled_doctypes([])  # Purchase Order not listed at all
        log = create_whatsapp_log(
            reference_doctype="Purchase Order", reference_name=po.name,
            supplier=po.supplier, contact=None, mobile_no="919000000009",
            result=self._failed(), template_name="yrp_wa_send", language_code="en",
            message_variables={"header_vars": [], "body_vars": [po.name, po.supplier]})

        with patch(DELIVER) as mock_deliver:
            with self.assertRaises(frappe.ValidationError) as ctx:
                whatsapp_notification.resend_whatsapp_notification_log(log.name)
        self.assertIn("not enabled", str(ctx.exception))
        mock_deliver.assert_not_called()   # blocked before any resend

    def test_resend_updates_the_same_row(self):
        from yrp.yrp.doctype.whatsapp_notification_log.whatsapp_notification_log import (
            create_whatsapp_log,
        )
        po = self._submitted_po()
        self._configure_enabled_doctypes([("Purchase Order", "supplier", 1)])
        log = create_whatsapp_log(
            reference_doctype="Purchase Order", reference_name=po.name,
            supplier=po.supplier, contact=None, mobile_no="919000000009",
            result=self._failed(), template_name="yrp_wa_send", language_code="en",
            message_variables={"header_vars": [], "body_vars": [po.name, po.supplier]})
        self.assertEqual(log.status, "Failed")

        with patch(DELIVER, return_value=self._ok("wamid.RESEND")) as mock_deliver:
            whatsapp_notification.resend_whatsapp_notification_log(log.name)
        mock_deliver.assert_called_once()
        # rebuilt from the stored blob + (template_name, language_code)
        kwargs = mock_deliver.call_args.kwargs
        self.assertEqual(kwargs["template_name"], "yrp_wa_send")
        self.assertEqual(kwargs["language_code"], "en")
        self.assertEqual(kwargs["body_vars"], [po.name, po.supplier])

        log.reload()
        self.assertEqual(log.status, "Sent")
        self.assertEqual(log.meta_message_id, "wamid.RESEND")
        self.assertTrue(log.sent_at)
