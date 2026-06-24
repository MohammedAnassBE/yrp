# Copyright (c) 2026, Essdee and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


def _tnc(po=0, wo=0, company=0):
	return frappe.get_doc(
		{
			"doctype": "Terms and Condition",
			"terms_and_condition_name": f"_Test TnC {frappe.generate_hash(length=8)}",
			"is_default_po_term": po,
			"is_default_wo_term": wo,
			"is_default_company": company,
		}
	).insert(ignore_permissions=True)


def _supplier(po_tc=None, wo_tc=None):
	return frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": f"_Test Supplier {frappe.generate_hash(length=8)}",
			"po_terms_and_condition": po_tc,
			"wo_terms_and_condition": wo_tc,
		}
	).insert(ignore_permissions=True)


def _flag(name, flag):
	return frappe.db.get_value("Terms and Condition", name, flag)


class TestTermsAndConditionDefaults(FrappeTestCase):
	# ---- single-default guard ("default things are always single") ----
	def test_setting_default_po_unsets_it_on_the_previous_one(self):
		first = _tnc(po=1)
		second = _tnc(po=1)
		self.assertEqual(_flag(first.name, "is_default_po_term"), 0)
		self.assertEqual(_flag(second.name, "is_default_po_term"), 1)

	def test_default_po_and_default_wo_flags_are_independent(self):
		po_default = _tnc(po=1)
		wo_default = _tnc(wo=1)
		# setting a WO default must not disturb the existing PO default
		self.assertEqual(_flag(po_default.name, "is_default_po_term"), 1)
		self.assertEqual(_flag(wo_default.name, "is_default_wo_term"), 1)

	def test_setting_default_company_unsets_it_on_the_previous_one(self):
		first = _tnc(company=1)
		second = _tnc(company=1)
		self.assertEqual(_flag(first.name, "is_default_company"), 0)
		self.assertEqual(_flag(second.name, "is_default_company"), 1)

	# ---- cascade resolver: supplier -> default-for-doctype -> company -> None ----
	def test_cascade_supplier_po_mapping_wins(self):
		from yrp.yrp.doctype.terms_and_condition.terms_and_condition import get_default_terms

		mapped = _tnc()
		_tnc(po=1)  # a PO default also exists, but the supplier mapping must win
		supplier = _supplier(po_tc=mapped.name)
		self.assertEqual(get_default_terms("PO", supplier.name), mapped.name)

	def test_cascade_falls_back_to_default_po_term(self):
		from yrp.yrp.doctype.terms_and_condition.terms_and_condition import get_default_terms

		po_default = _tnc(po=1)
		supplier = _supplier()  # no supplier mapping
		self.assertEqual(get_default_terms("PO", supplier.name), po_default.name)

	def test_cascade_falls_back_to_company_default(self):
		from yrp.yrp.doctype.terms_and_condition.terms_and_condition import get_default_terms

		company_default = _tnc(company=1)
		supplier = _supplier()  # no mapping, no PO default
		self.assertEqual(get_default_terms("PO", supplier.name), company_default.name)

	def test_cascade_returns_none_when_nothing_configured(self):
		from yrp.yrp.doctype.terms_and_condition.terms_and_condition import get_default_terms

		# "nothing configured" = no term anywhere holds a default flag, and no
		# supplier mapping. Don't insert a Supplier here (its autoname commit would
		# leak this flag-reset), so the reset stays inside the test transaction.
		frappe.db.sql(
			"UPDATE `tabTerms and Condition` "
			"SET is_default_po_term = 0, is_default_wo_term = 0, is_default_company = 0"
		)
		self.assertIsNone(get_default_terms("PO", None))
		self.assertIsNone(get_default_terms("WO", "Nonexistent Supplier XYZ"))

	def test_wo_cascade_uses_supplier_wo_mapping(self):
		from yrp.yrp.doctype.terms_and_condition.terms_and_condition import get_default_terms

		wo_mapped = _tnc()
		supplier = _supplier(wo_tc=wo_mapped.name)
		self.assertEqual(get_default_terms("WO", supplier.name), wo_mapped.name)

	def test_wo_cascade_falls_back_to_default_wo_term(self):
		from yrp.yrp.doctype.terms_and_condition.terms_and_condition import get_default_terms

		wo_default = _tnc(wo=1)
		supplier = _supplier()
		self.assertEqual(get_default_terms("WO", supplier.name), wo_default.name)

	# ---- Purchase Order prefill: before_validate, is_new() + only-if-empty ----
	def test_po_prefills_terms_on_new_when_empty(self):
		mapped = _tnc()
		supplier = _supplier(po_tc=mapped.name)
		po = frappe.new_doc("Purchase Order")
		po.supplier = supplier.name
		po.set_default_terms()
		self.assertEqual(po.terms_and_condition, mapped.name)

	def test_po_does_not_override_a_chosen_term(self):
		mapped = _tnc()
		chosen = _tnc()
		supplier = _supplier(po_tc=mapped.name)
		po = frappe.new_doc("Purchase Order")
		po.supplier = supplier.name
		po.terms_and_condition = chosen.name
		po.set_default_terms()
		self.assertEqual(po.terms_and_condition, chosen.name)

	# ---- Work Order prefill ----
	def test_wo_prefills_terms_on_new_when_empty(self):
		mapped = _tnc()
		supplier = _supplier(wo_tc=mapped.name)
		wo = frappe.new_doc("Work Order")
		wo.supplier = supplier.name
		wo.set_default_terms()
		self.assertEqual(wo.terms_and_condition, mapped.name)
