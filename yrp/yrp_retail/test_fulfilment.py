"""Native multi-order delivery/invoice mapping with fictional non-stock Items."""
import frappe
from frappe.utils import nowdate, add_days, getdate
from yrp.yrp_retail.test_retail_flow import TestRetailFlow
from yrp.yrp_retail.fulfilment import make_delivery_note, make_sales_invoice


class TestFulfilment(TestRetailFlow):
	def setUp(self):
		super().setUp()
		frappe.set_user('Administrator')
		suffix=frappe.generate_hash(length=8)
		self.company=frappe.get_doc({'doctype':'Company','company_name':'Test Fulfilment '+suffix,
			'abbr':'TF'+suffix[:3],'country':'United States','default_currency':'USD',
			'chart_of_accounts':'Standard','enable_perpetual_inventory':0}).insert()
		frappe.get_doc({'doctype':'Fiscal Year','year':'Test Fulfilment Year '+suffix,
			'year_start_date':str(getdate(nowdate()).year)+'-01-01','year_end_date':str(getdate(nowdate()).year)+'-12-31',
			'companies':[{'company':self.company.name}]}).insert()
		self.price_list=frappe.get_doc({'doctype':'Price List','price_list_name':'Test Fulfilment Prices '+suffix,
			'currency':'USD','enabled':1,'selling':1}).insert()
		frappe.get_doc({'doctype':'Item Price','item_code':self.item.name,'uom':self.uom.name,
			'price_list':self.price_list.name,'price_list_rate':20}).insert()

	def sales_order(self,qty=5,taxes=None):
		doc=frappe.get_doc({'doctype':'Sales Order','customer':self.customer.name,'company':self.company.name,
			'transaction_date':nowdate(),'delivery_date':add_days(nowdate(),1),'currency':'USD',
			'selling_price_list':self.price_list.name,'price_list_currency':'USD','conversion_rate':1,'plc_conversion_rate':1,
			'taxes':taxes or [],
			'items':[{'item_code':self.item.name,'qty':qty,'uom':self.uom.name,'conversion_factor':1,'rate':20}]})
		doc.insert();doc.submit()
		return doc

	def test_multiple_orders_keep_native_line_links_and_invoice_rates(self):
		orders=[self.sales_order(5),self.sales_order(3)]
		result=make_delivery_note(self.customer.name,[o.name for o in orders])
		dn=frappe.get_doc('Delivery Note',result['name'])
		self.assertEqual(dn.docstatus,0)
		self.assertEqual({r.against_sales_order for r in dn.items},{o.name for o in orders})
		self.assertEqual(sum(r.qty for r in dn.items),8)
		dn.submit()
		invoice=frappe.get_doc('Sales Invoice',make_sales_invoice(dn.name)['name'])
		self.assertEqual(invoice.docstatus,0)
		self.assertFalse(invoice.update_stock)
		self.assertEqual({r.dn_detail for r in invoice.items},{r.name for r in dn.items})
		self.assertTrue(all(r.rate==20 for r in invoice.items))
		self.assertFalse(frappe.db.exists('Stock Ledger Entry',{'voucher_no':dn.name}))

	def test_partial_delivery_and_bad_customer_or_quantities(self):
		order=self.sales_order(5)
		for qty in (0,-1,6):
			with self.assertRaises(frappe.ValidationError):
				make_delivery_note(self.customer.name,[order.name],[{'sales_order_item':order.items[0].name,'qty':qty}])
		with self.assertRaises(frappe.ValidationError):make_delivery_note('Wrong Customer',[order.name])
		with self.assertRaises(frappe.ValidationError):make_delivery_note(self.customer.name,[order.name,order.name])
		result=make_delivery_note(self.customer.name,[order.name],[{'sales_order_item':order.items[0].name,'qty':2}])
		dn=frappe.get_doc('Delivery Note',result['name'])
		self.assertEqual(dn.items[0].qty,2)

	def test_invoice_requires_submitted_delivery_and_guest_cannot_create(self):
		order=self.sales_order()
		dn=make_delivery_note(self.customer.name,[order.name])['name']
		with self.assertRaises(frappe.ValidationError):make_sales_invoice(dn)
		frappe.set_user('Guest')
		with self.assertRaises(frappe.PermissionError):make_delivery_note(self.customer.name,[order.name])

	def test_consolidation_rejects_different_manual_taxes_and_fixed_charges(self):
		account=frappe.get_doc({'doctype':'Account','account_name':self.label('Tax'),
			'company':self.company.name,'account_type':'Tax',
			'parent_account':frappe.db.get_value('Account',{'company':self.company.name,
				'root_type':'Liability','is_group':1},'name')}).insert()
		def tax(rate,actual=False):
			return [{'charge_type':'Actual' if actual else 'On Net Total','account_head':account.name,
				'description':'Fictional tax','rate':rate,'tax_amount':5 if actual else 0}]
		first=self.sales_order(taxes=tax(5))
		second=self.sales_order(taxes=tax(10))
		before=frappe.db.count('Delivery Note')
		with self.assertRaises(frappe.ValidationError):
			make_delivery_note(self.customer.name,[first.name,second.name])
		self.assertEqual(frappe.db.count('Delivery Note'),before)
		matching=self.sales_order(taxes=tax(5))
		dn=frappe.get_doc('Delivery Note',make_delivery_note(self.customer.name,[first.name,matching.name])['name'])
		self.assertEqual(dn.taxes[0].rate,5)
		self.assertEqual(dn.total_taxes_and_charges,10)
		fixed=[self.sales_order(taxes=tax(0,True)) for _ in range(2)]
		with self.assertRaises(frappe.ValidationError):
			make_delivery_note(self.customer.name,[order.name for order in fixed])
