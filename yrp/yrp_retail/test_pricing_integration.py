"""Native Item Price saves and native sales calculations using fictional fixtures.

Every fixture rolls back through TestCreateItemFromTemplate's savepoint. No
existing customer, company, price, account, or transactional business data is used.
"""

import frappe

from yrp.yrp.doctype.yrp_item_master_template.test_yrp_item_master_template import TestCreateItemFromTemplate
from yrp.yrp.doctype.yrp_item_master_template.yrp_item_master_template import create_item_from_template


class TestRetailPricingIntegration(TestCreateItemFromTemplate):
	def make_item(self, free=False):
		self.template.is_free_item = int(free)
		self.template.save()
		name = create_item_from_template(self.template.name, self.item_name, self.group.name, self.hsn.name)
		item = frappe.get_doc("Item", name)
		self.assertEqual(bool(item.yrp_is_free_item), free)
		return item

	def make_list(self, selling=True):
		return frappe.get_doc({
			"doctype": "Price List", "price_list_name": "Test Pricing " + frappe.generate_hash(length=10),
			"enabled": 1, "selling": int(selling), "buying": int(not selling), "currency": "INR",
		}).insert()

	def price(self, item, price_list, rate):
		return frappe.get_doc({
			"doctype": "Item Price", "item_code": item.name, "price_list": price_list.name,
			"uom": self.uom.name, "price_list_rate": rate,
		}).insert()

	def test_native_paid_item_prices(self):
		item = self.make_item()
		price_list = self.make_list()
		for rate in (0, -1):
			with self.assertRaises(frappe.ValidationError):
				self.price(item, price_list, rate)
		price = self.price(item, price_list, 25)
		self.assertEqual(price.price_list_rate, 25)
		price.price_list_rate = 0
		with self.assertRaises(frappe.ValidationError):
			price.save()

	def test_native_free_item_prices_and_purchase_prices(self):
		item = self.make_item(free=True)
		price_list = self.make_list()
		with self.assertRaises(frappe.ValidationError):
			self.price(item, price_list, 25)
		self.assertEqual(self.price(item, price_list, 0).price_list_rate, 0)
		self.assertEqual(self.price(item, self.make_list(selling=False), 18).price_list_rate, 18)

	def test_native_template_flip_requires_removing_incompatible_prices(self):
		item = self.make_item()
		price = self.price(item, self.make_list(), 25)
		self.template.is_free_item = 1
		with self.assertRaises(frappe.ValidationError):
			self.template.save()
		self.assertEqual(frappe.db.get_value("Item", item.name, "yrp_is_free_item"), 0)
		price.delete()
		self.template.reload()
		self.template.is_free_item = 1
		self.template.save()
		self.assertEqual(frappe.db.get_value("Item", item.name, "yrp_is_free_item"), 1)

	def test_native_buying_list_cannot_bypass_selling_policy(self):
		item = self.make_item()
		price_list = self.make_list(selling=False)
		self.price(item, price_list, 0)
		price_list.selling = 1
		with self.assertRaises(frappe.ValidationError):
			price_list.save()

	def test_native_calculations_for_free_paid_and_discounted_rows(self):
		item = self.make_item(free=True)
		suffix = frappe.generate_hash(length=8)
		company = frappe.get_doc({
			"doctype": "Company", "company_name": "Test Pricing Company " + suffix,
			"abbr": "TP" + suffix[:3], "country": "India", "default_currency": "INR",
			"chart_of_accounts": "Standard", "enable_perpetual_inventory": 0,
		}).insert()
		group = frappe.get_doc({
			"doctype": "Customer Group", "customer_group_name": "Test Pricing Group " + suffix,
			"parent_customer_group": frappe.db.get_value("Customer Group", {"lft": 1}, "name"),
		}).insert()
		territory = frappe.get_doc({
			"doctype": "Territory", "territory_name": "Test Pricing Territory " + suffix,
			"parent_territory": frappe.db.get_value("Territory", {"lft": 1}, "name"),
		}).insert()
		customer = frappe.get_doc({
			"doctype": "Customer", "customer_name": "Test Pricing Customer " + suffix,
			"customer_type": "Individual",
			"customer_group": group.name,
			"territory": territory.name,
		}).insert()

		def document(doctype, rate=25):
			return frappe.get_doc({
				"doctype": doctype, "company": company.name, "customer": customer.name,
				"currency": "INR", "conversion_rate": 1, "plc_conversion_rate": 1,
				"selling_price_list": self.make_list().name, "price_list_currency": "INR",
				"ignore_pricing_rule": 1, "disable_rounded_total": 1,
				"items": [{"item_code": item.name, "item_name": item.item_name,
					"qty": 2, "uom": item.stock_uom, "stock_uom": item.stock_uom,
					"conversion_factor": 1, "rate": rate, "price_list_rate": rate,
					"margin_type": "Amount", "margin_rate_or_amount": 5}],
			})

		for doctype in ("Sales Order", "Delivery Note", "Sales Invoice"):
			with self.subTest(doctype=doctype, policy="free"):
				doc = document(doctype)
				doc.calculate_taxes_and_totals()
				self.assertEqual(doc.items[0].rate, 0)
				self.assertEqual(doc.items[0].net_rate, 0)
				self.assertEqual(doc.grand_total, 0)
		self.template.is_free_item = 0
		self.template.save()
		for doctype in ("Sales Order", "Delivery Note", "Sales Invoice"):
			with self.subTest(doctype=doctype, policy="paid"):
				doc = document(doctype)
				doc.calculate_taxes_and_totals()
				self.assertGreater(doc.items[0].net_rate, 0)
				self.assertGreater(doc.grand_total, 0)
				doc.apply_discount_on = "Net Total"
				doc.additional_discount_percentage = 100
				with self.assertRaises(frappe.ValidationError):
					doc.calculate_taxes_and_totals()
