"""Tests for Inspection Entry — new model (2026-05-19+):

  - Submit locks the form but does NOT write SLEs.
  - Convert Stock writes SLEs (gated by `YRP Settings.inspection_entry_approver_role`).
  - Multiple IEs per source doc (GRN or Material-Receipt Stock Entry) are allowed.
  - `_validate_bin_balance` + the stock engine's NegativeStockError are the only
    cross-IE safeguards — no explicit cross-IE total cap.
  - Cancel allowed only before Convert.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_default_received_type,
	_purchase_order,
	_purchase_order_grn,
	_warehouse,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _received_type(name):
	if frappe.db.exists("Received Type", name):
		return name
	doc = frappe.get_doc({
		"doctype": "Received Type",
		"received_type_name": name,
		"is_default": 0,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def _submitted_grn(qty=10):
	warehouse = _warehouse(f"_T_IE_WH_{frappe.generate_hash(length=6)}")
	po = _purchase_order(qty=qty, warehouse=warehouse)
	grn = _purchase_order_grn(po, qty=qty)
	grn.submit()
	return grn


def _new_ie_from_grn(grn):
	"""Build an IE doc with rows pre-populated via the same code path the form
	uses (`get_initial_payload` → `_ungroup_items_from_ui`)."""
	from yrp.yrp.doctype.inspection_entry.inspection_entry import (
		_ungroup_items_from_ui,
		get_initial_payload,
	)

	sources = get_initial_payload(against="Goods Received Note", against_id=grn.name)
	doc = frappe.get_doc({
		"doctype": "Inspection Entry",
		"against": "Goods Received Note",
		"against_id": grn.name,
		"posting_date": nowdate(),
		"inspector": frappe.session.user or "Administrator",
		"items": [],
	})
	for r in _ungroup_items_from_ui(sources):
		doc.append("items", r)
	return doc


def _configure_approver_role(role="System Manager"):
	"""Ensure YRP Settings has an approver role configured for the current user."""
	settings = frappe.get_single("YRP Settings")
	settings.db_set("inspection_entry_approver_role", role)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
class TestInspectionEntry(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_default_received_type()
		cls.rejected_rt = _received_type("Rejected")

	# ---------- Initial-payload behavior ----------

	def test_01_initial_payload_returns_grn_rows_with_display_meta(self):
		"""get_initial_payload returns one source per GRN line, each with display_meta."""
		from yrp.yrp.doctype.inspection_entry.inspection_entry import get_initial_payload

		grn = _submitted_grn(qty=10)
		sources = get_initial_payload(against="Goods Received Note", against_id=grn.name)
		self.assertEqual(len(sources), len(grn.items))
		for s in sources:
			self.assertIn("display_meta", s)
			meta = s["display_meta"]
			self.assertIn("group_key", meta)
			self.assertIn("primary_attribute_values", meta)
			self.assertIn("primary_attribute_value", meta)
		# Default split: one row per source, target=source, qty=grn_qty.
		for s in sources:
			self.assertEqual(len(s["splits"]), 1)
			self.assertEqual(s["splits"][0]["target_received_type"], s["source_received_type"])

	def test_02_initial_payload_full_qty_even_after_prior_ie(self):
		"""Cross-IE subtraction was removed: a second IE against the same GRN still
		sees the GRN's original qty for every row. The per-bin stock-balance check
		(at Convert Stock time) is the real safeguard."""
		from yrp.yrp.doctype.inspection_entry.inspection_entry import get_initial_payload

		grn = _submitted_grn(qty=10)
		sources_before = get_initial_payload("Goods Received Note", grn.name)

		ie1 = _new_ie_from_grn(grn)
		ie1.insert(ignore_permissions=True)
		ie1.submit()

		sources_after = get_initial_payload("Goods Received Note", grn.name)
		self.assertEqual(len(sources_after), len(sources_before))
		for a, b in zip(sources_before, sources_after):
			self.assertEqual(a["grn_qty"], b["grn_qty"])
			self.assertEqual(a["ref_docname"], b["ref_docname"])

	# ---------- Submit does NOT move stock ----------

	def test_03_submit_writes_no_sles(self):
		grn = _submitted_grn(qty=10)
		ie = _new_ie_from_grn(grn)
		ie.insert(ignore_permissions=True)
		ie.submit()
		ie.reload()
		self.assertEqual(ie.docstatus, 1)
		self.assertEqual(ie.status, "Submitted")
		self.assertEqual(int(ie.is_converted or 0), 0)
		sles = frappe.db.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": ie.name, "is_cancelled": 0},
		)
		self.assertEqual(len(sles), 0)

	# ---------- Multiple IEs per GRN ----------

	def test_04_multiple_ies_per_grn(self):
		"""Two independent IEs can be created, inserted and submitted against
		the same GRN — the previous "unique IE per GRN" guard is gone."""
		grn = _submitted_grn(qty=10)

		ie1 = _new_ie_from_grn(grn)
		ie1.insert(ignore_permissions=True)
		ie1.submit()
		self.assertEqual(ie1.docstatus, 1)

		ie2 = _new_ie_from_grn(grn)
		ie2.insert(ignore_permissions=True)
		ie2.submit()
		self.assertEqual(ie2.docstatus, 1)

		self.assertNotEqual(ie1.name, ie2.name)
		self.assertEqual(
			frappe.db.count("Inspection Entry",
				{"against_id": grn.name, "docstatus": 1}),
			2,
		)

	# ---------- Convert Stock writes SLEs ----------

	def test_05_convert_stock_writes_sles_and_flips_flag(self):
		"""After Convert Stock, SLEs exist for every target!=source row and
		`is_converted` is True. `status` becomes "Converted"."""
		from yrp.yrp.doctype.inspection_entry.inspection_entry import convert_stock

		grn = _submitted_grn(qty=10)
		ie = _new_ie_from_grn(grn)
		# Split one row into source + rejected.
		default_row = ie.items[0]
		default_row.qty = 7
		ie.append("items", {
			"item_variant": default_row.item_variant,
			"warehouse": default_row.warehouse,
			"received_type": default_row.received_type,
			"lot": default_row.get("lot"),
			"grn_qty": default_row.grn_qty,
			"qty": 3,
			"target_received_type": self.rejected_rt,
			"received_date": nowdate(),
			"ref_doctype": default_row.ref_doctype,
			"ref_docname": default_row.ref_docname,
		})
		ie.insert(ignore_permissions=True)
		ie.submit()

		_configure_approver_role("System Manager")
		convert_stock(ie.name)
		ie.reload()
		self.assertEqual(ie.status, "Converted")
		self.assertEqual(int(ie.is_converted), 1)

		sles = frappe.db.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": ie.name, "is_cancelled": 0},
			fields=["received_type", "qty"],
		)
		balances = {s.received_type: flt(s.qty) for s in sles}
		self.assertAlmostEqual(balances.get("Rejected", 0), 3, places=3)
		self.assertAlmostEqual(balances.get("Accepted", 0), -3, places=3)

	# ---------- Cancel rules ----------

	def test_06_cancel_allowed_before_convert(self):
		grn = _submitted_grn(qty=10)
		ie = _new_ie_from_grn(grn)
		ie.insert(ignore_permissions=True)
		ie.submit()
		ie.reload()
		ie.cancel()
		ie.reload()
		self.assertEqual(ie.docstatus, 2)
		self.assertEqual(ie.status, "Cancelled")

	def test_07_cancel_blocked_after_convert(self):
		from yrp.yrp.doctype.inspection_entry.inspection_entry import convert_stock

		grn = _submitted_grn(qty=10)
		ie = _new_ie_from_grn(grn)
		ie.insert(ignore_permissions=True)
		ie.submit()

		_configure_approver_role("System Manager")
		convert_stock(ie.name)
		ie.reload()

		with self.assertRaisesRegex(frappe.ValidationError, "already converted stock"):
			ie.cancel()

	# ---------- Approver gating ----------

	def test_08_convert_stock_requires_configured_role(self):
		from yrp.yrp.doctype.inspection_entry.inspection_entry import convert_stock

		grn = _submitted_grn(qty=10)
		ie = _new_ie_from_grn(grn)
		ie.insert(ignore_permissions=True)
		ie.submit()

		# No role configured at all.
		frappe.db.set_single_value("YRP Settings", "inspection_entry_approver_role", "")
		with self.assertRaisesRegex(frappe.ValidationError, "not configured"):
			convert_stock(ie.name)
