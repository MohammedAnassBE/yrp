"""Synthetic Customer counts: replacement, authorization and summary consumption."""
import frappe
from yrp.yrp_retail import api
from yrp.yrp_retail.test_retail_flow import TestRetailFlow


class TestCustomerStock(TestRetailFlow):
	def setUp(self):
		super().setUp()
		# Count entry is explicitly enabled by this test's custom-app policy.
		frappe.set_user("Administrator")
		from frappe.permissions import add_permission, update_permission_property
		add_permission("YRP Customer Stock", "YRP Sales Person")
		update_permission_property("YRP Customer Stock", "YRP Sales Person", 0, "write", 1)
		frappe.set_user(self.user.name)

	def stock(self):
		return api.get_customer_stock(self.customer.name, self.item.name)

	def report(self, qty):
		return api.update_customer_stock(self.customer.name, self.item.name, qty)

	def summary(self, allocated=10):
		return api.create_summary([self.order(12)], [{'item_code':self.item.name,
			'uom':self.uom.name, 'customer_stock_qty':allocated}])['name']

	def test_absolute_count_replaces_one_shared_balance(self):
		self.assertFalse(self.stock()['recorded'])
		self.assertEqual(self.report(6)['qty'], 6)
		self.assertEqual(self.report(4)['qty'], 4)
		self.assertTrue(self.stock()['recorded'])
		self.assertEqual(frappe.db.count('YRP Customer Stock', {'customer':self.customer.name, 'item_code':self.item.name}), 1)
		self.assertEqual(self.stock()['last_counted_by'], self.user.name)

	def test_six_available_ten_allocated_clamps_at_zero_on_submit(self):
		self.report(6)
		name = self.summary(10)
		self.assertEqual(self.stock()['qty'], 6)
		api.update_summary(name, [{'item_code':self.item.name, 'uom':self.uom.name, 'customer_stock_qty':10}])
		self.assertEqual(self.stock()['qty'], 6)
		api.submit_summary(name)
		self.assertEqual(self.stock()['qty'], 0)
		self.assertEqual(frappe.db.get_value('YRP Retail Order Summary', name, 'total_company_qty'), 2)
		with self.assertRaises(frappe.ValidationError):
			api.submit_summary(name)
		self.assertEqual(self.stock()['qty'], 0)

	def test_allocation_without_a_report_is_allowed(self):
		api.submit_summary(self.summary(10))
		self.assertEqual(self.stock()['qty'], 0)
		self.assertFalse(self.stock()['recorded'])
		self.assertEqual(self.report(7)['qty'], 7)

	def test_latest_count_and_sequential_summaries(self):
		self.report(20)
		first = self.summary(3)
		self.report(8)
		api.submit_summary(first)
		self.assertEqual(self.stock()['qty'], 5)
		api.submit_summary(self.summary(4))
		self.assertEqual(self.stock()['qty'], 1)

	def test_cancel_does_not_fabricate_a_physical_receipt(self):
		self.report(6)
		name = self.summary(2)
		api.submit_summary(name)
		self.report(9)
		frappe.set_user('Administrator')
		frappe.get_doc('YRP Retail Order Summary', name).cancel()
		frappe.set_user(self.user.name)
		self.assertEqual(self.stock()['qty'], 9)

	def test_uom_is_not_converted_and_cannot_change_existing_balances(self):
		frappe.set_user('Administrator')
		other = frappe.get_doc({'doctype':'UOM', 'uom_name':self.label('Boxes')}).insert()
		frappe.db.set_single_value('YRP Retail Settings', 'stock_uom', other.name)
		frappe.set_user(self.user.name)
		self.assertEqual(self.report(6)['qty'], 6)
		self.assertEqual(self.stock()['uom'], other.name)
		frappe.set_user('Administrator')
		settings = frappe.get_doc('YRP Retail Settings')
		settings.stock_uom = self.uom.name
		with self.assertRaises(frappe.ValidationError): settings.save()

	def test_invalid_counts_and_unassigned_customer_are_rejected(self):
		for qty in (-1, 'bad', float('nan'), float('inf')):
			with self.assertRaises(frappe.ValidationError): self.report(qty)
		frappe.db.set_value('UOM', self.uom.name, 'must_be_whole_number', 1)
		with self.assertRaises(frappe.ValidationError): self.report(1.5)
		for method in (api.get_customer_stock, api.update_customer_stock):
			kwargs = {'qty':6} if method == api.update_customer_stock else {}
			with self.assertRaises(frappe.PermissionError): method('Unassigned Customer', self.item.name, **kwargs)

	def test_direct_writes_fail_but_assigned_stock_is_readable(self):
		self.report(6)
		names = frappe.get_list('YRP Customer Stock', filters={'customer':self.customer.name}, pluck='name')
		self.assertEqual(len(names), 1)
		doc = frappe.get_doc('YRP Customer Stock', names[0])
		doc.qty = 900
		with self.assertRaises(frappe.PermissionError): doc.save(ignore_permissions=True)
		self.assertEqual(self.stock()['qty'], 6)

	def test_missing_settings_and_wrong_order_uom_are_rejected(self):
		frappe.db.set_single_value('YRP Retail Settings', 'stock_uom', None)
		with self.assertRaises(frappe.ValidationError): self.report(6)
		frappe.db.set_single_value('YRP Retail Settings', 'stock_uom', 'Different UOM')
		with self.assertRaises(frappe.ValidationError): self.order()

	def test_partner_manager_role_cannot_change_settings(self):
		frappe.set_user('Administrator')
		user = frappe.get_doc('User', self.user.name)
		user.append('roles', {'role':'YRP Partner Manager'})
		user.save()
		frappe.set_user(self.user.name)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc('YRP Retail Settings').save(ignore_permissions=True)

	def test_failed_submission_rolls_back_status_and_balance(self):
		self.report(6)
		name = self.summary(2)
		frappe.db.set_single_value('YRP Retail Settings', 'stock_uom', None)
		with self.assertRaises(frappe.ValidationError): api.submit_summary(name)
		self.assertEqual(frappe.db.get_value('YRP Retail Order Summary', name, 'docstatus'), 0)
		self.assertEqual(frappe.db.get_value('YRP Customer Stock', {'customer':self.customer.name, 'item_code':self.item.name}, 'qty'), 6)
