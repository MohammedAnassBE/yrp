from unittest import TestCase

import frappe
from frappe.model.workflow import (
	WorkflowTransitionError,
	apply_workflow,
	get_transitions,
	get_workflow,
)
from frappe.utils import nowdate

from yrp.stock.test_uom import _ensure_uom, _simple_item_variant


class TestYRPProcessCost(TestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.clear_messages)
		frappe.set_user("Administrator")
		self.suffix = frappe.generate_hash(length=12)
		self.savepoint = "process_cost_" + self.suffix
		frappe.db.savepoint(self.savepoint)
		self.addCleanup(frappe.db.rollback, save_point=self.savepoint)
		self.addCleanup(frappe.set_user, "Administrator")
		self.assertEqual(get_workflow("YRP Process Cost").name, "Process Cost Workflow")
		self.purchase_user = self.make_user("user", "Purchase User")
		self.purchase_manager = self.make_user("manager", "Purchase Manager")
		self.other_user = self.make_user("other", "System Manager")
		self.uom = _ensure_uom("Nos")
		self.item = _simple_item_variant(self.uom).name
		self.supplier = (
			frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": f"_Test Process Cost Supplier {self.suffix}",
					"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.process = (
			frappe.get_doc({"doctype": "YRP Process", "process_name": f"_Test Cost Process {self.suffix}"})
			.insert(ignore_permissions=True)
			.name
		)
		self.tax = frappe.db.get_value("YRP Tax Slab", {"enabled": 1}, "name")
		if not self.tax:
			self.tax = (
				frappe.get_doc(
					{
						"doctype": "YRP Tax Slab",
						"percentage": f"0.{frappe.utils.now_datetime().strftime('%f')}",
						"enabled": 1,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def make_user(self, label, role):
		return (
			frappe.get_doc(
				{
					"doctype": "User",
					"email": f"process-cost-{label}-{self.suffix}@example.test",
					"first_name": f"Process Cost {label}",
					"enabled": 1,
					"user_type": "System User",
					"send_welcome_email": 0,
					"roles": [{"role": role}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_rejected_cost(self):
		doc = frappe.get_doc(
			{
				"doctype": "YRP Process Cost",
				"item": self.item,
				"supplier": self.supplier,
				"process_name": self.process,
				"uom": self.uom,
				"from_date": nowdate(),
				"tax_slab": self.tax,
				"depends_on_attribute": 0,
				"process_cost_values": [{"price": 10, "min_order_qty": 0}],
			}
		)
		if frappe.get_meta("YRP Process Cost").has_field("lot"):
			doc.lot = frappe.db.get_value("SD YRP Lot", {"item": ["is", "set"]}, "name")
			if not doc.lot:
				self.skipTest("No existing SD YRP Lot with an item is available")
		doc.insert()
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Draft", 0))
		frappe.set_user(self.purchase_user)
		doc = apply_workflow(doc, "Submit")
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Approval Pending", 0))
		frappe.set_user(self.purchase_manager)
		doc = apply_workflow(doc, "Reject")
		doc.reload()
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Rejected", 0))
		self.assertFalse(doc.approved_by)
		return doc

	def test_purchase_user_can_revise_and_resubmit_rejected_cost(self):
		doc = self.make_rejected_cost()
		frappe.set_user(self.purchase_user)
		doc = apply_workflow(doc, "Revise")
		doc.reload()
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Draft", 0))
		self.assertFalse(doc.approved_by)
		doc = apply_workflow(doc, "Submit")
		doc.reload()
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Approval Pending", 0))

	def test_purchase_manager_can_revise_rejected_cost(self):
		doc = self.make_rejected_cost()
		frappe.set_user(self.purchase_manager)
		self.assertNotIn("Purchase User", frappe.get_roles())
		doc = apply_workflow(doc, "Revise")
		doc.reload()
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Draft", 0))
		self.assertFalse(doc.approved_by)

	def test_user_without_purchase_roles_cannot_revise_rejected_cost(self):
		doc = self.make_rejected_cost()
		frappe.set_user(self.other_user)
		self.assertTrue({"Purchase User", "Purchase Manager"}.isdisjoint(frappe.get_roles()))
		self.assertTrue(frappe.has_permission("YRP Process Cost", "read", doc=doc))
		self.assertTrue(frappe.has_permission("YRP Process Cost", "write", doc=doc))
		with self.assertRaises(WorkflowTransitionError):
			apply_workflow(doc, "Revise")
		doc.reload()
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Rejected", 0))

	def test_rejected_cost_offers_revise_to_purchase_user(self):
		doc = self.make_rejected_cost()
		frappe.set_user(self.purchase_user)
		self.assertIn("Revise", [transition.action for transition in get_transitions(doc)])
