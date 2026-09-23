"""Native template inheritance with fictional company accounts and tax mappings."""
import frappe
from yrp.yrp.doctype.yrp_item_master_template.test_yrp_item_master_template import TestCreateItemFromTemplate
from yrp.yrp.doctype.yrp_item_master_template.yrp_item_master_template import create_item_from_template


class TestTemplateSalesDefaults(TestCreateItemFromTemplate):
	def create_item(self):
		return frappe.get_doc('Item', create_item_from_template(self.template.name, self.item_name, self.group.name, self.hsn.name))

	def test_link_and_free_status_are_template_owned(self):
		self.template.is_free_item = 1
		self.template.save()
		item = self.create_item()
		self.assertEqual(item.yrp_item_master_template, self.template.name)
		self.assertEqual(item.yrp_is_free_item, 1)
		item.yrp_is_free_item = 0
		item.save()
		self.assertEqual(item.yrp_is_free_item, 1)
		self.template.is_free_item = 0
		self.template.save()
		item.reload()
		self.assertEqual(item.yrp_is_free_item, 0)
		item.yrp_item_master_template = None
		with self.assertRaises(frappe.ValidationError): item.save()

	def test_native_tax_and_company_defaults_inherit_and_sync(self):
		suffix = frappe.generate_hash(length=8)
		company = frappe.get_doc({'doctype':'Company', 'company_name':'Test Defaults '+suffix,
			'abbr':'TD'+suffix[:3], 'country':'India', 'default_currency':'INR',
			'chart_of_accounts':'Standard', 'enable_perpetual_inventory':0}).insert()
		income = frappe.db.get_value('Account', {'company':company.name,'root_type':'Income','is_group':0}, 'name')
		expense = frappe.db.get_value('Account', {'company':company.name,'root_type':'Expense','is_group':0}, 'name')
		self.assertTrue(income and expense)
		tax_account = frappe.get_doc({'doctype':'Account','account_name':'Test Tax '+suffix,
			'company':company.name,'parent_account':frappe.db.get_value('Account',{'company':company.name,'root_type':'Liability','is_group':1},'name'),
			'account_type':'Tax','is_group':0}).insert()
		tax = frappe.get_doc({'doctype':'Item Tax Template','title':'Test Tax Template '+suffix,
			'company':company.name,'gst_rate':5,'taxes':[{'tax_type':tax_account.name,'tax_rate':5}]}).insert()
		defaults = self.template.append('item_defaults',{})
		for field in defaults.meta.fields:
			if field.fieldtype == 'Link': defaults.set(field.fieldname,None)
		defaults.update({'company':company.name,'income_account':income,'expense_account':expense,
			'default_warehouse':frappe.db.get_value('Warehouse',{'company':company.name,'is_group':0},'name'),
			'buying_cost_center':frappe.db.get_value('Cost Center',{'company':company.name,'is_group':0},'name'),
			'selling_cost_center':frappe.db.get_value('Cost Center',{'company':company.name,'is_group':0},'name')})
		self.template.append('taxes',{'item_tax_template':tax.name})
		self.template.save()
		item = self.create_item()
		self.assertEqual(item.item_defaults[0].income_account, income)
		self.assertEqual(item.item_defaults[0].expense_account, expense)
		self.assertEqual(item.taxes[0].item_tax_template, tax.name)
		self.template.set('taxes',[])
		self.template.item_defaults[0].expense_account = None
		self.template.save()
		item.reload()
		self.assertFalse(item.taxes)
		self.assertFalse(item.item_defaults[0].expense_account)
		self.assertEqual(item.item_defaults[0].income_account,income)
		self.template.set('item_defaults',[])
		self.template.save()
		item.reload()
		self.assertFalse(item.item_defaults)

	def test_classification_copies_and_allows_future_seasons(self):
		def node(kind,parent=None):
			return frappe.get_doc({'doctype':'YRP Item Category','category_name':'Test '+kind+' '+frappe.generate_hash(length=8),
				'node_type':kind,'is_group':int(kind!='Value'),'parent_yrp_item_category':parent}).insert()
		root=node('Item Type');category=node('Category',root.name);value=node('Value',category.name)
		self.template.item_type=root.name
		self.template.append('categories',{'category':category.name,'value':value.name})
		self.template.save()
		item=self.create_item()
		self.assertEqual(item.yrp_item_type,root.name)
		self.assertEqual(item.yrp_categories[0].value,value.name)
		later_value=node('Value',category.name)
		self.assertTrue(later_value.name)
		item.reload()
		self.assertEqual(item.yrp_categories[0].value,value.name)

	def test_unreadable_template_cannot_be_linked_and_policy_merge_is_rejected(self):
		from yrp.yrp_retail.item_template import apply_template, guard_policy_merge
		item = self.create_item()
		user = frappe.get_doc({'doctype':'User','email':'test-template-'+frappe.generate_hash(length=10)+'@example.invalid',
			'first_name':'Fictional Reader','send_welcome_email':0,'roles':[{'role':'Stock User'}]}).insert()
		old_user = frappe.session.user
		try:
			frappe.set_user(user.name)
			new_item = frappe.new_doc('Item')
			new_item.yrp_item_master_template = self.template.name
			with self.assertRaises(frappe.PermissionError): apply_template(new_item)
		finally: frappe.set_user(old_user)
		other = frappe.copy_doc(item)
		other.item_code = 'Test Other '+frappe.generate_hash(length=10)
		other.yrp_item_master_template = None
		other.insert()
		with self.assertRaises(frappe.ValidationError):
			guard_policy_merge(item, old=item.name, new=other.name, merge=True)
		with self.assertRaises(frappe.ValidationError):
			guard_policy_merge(self.template, old=self.template.name, new='Another Template', merge=True)

	def test_merge_guard_checks_current_source_policy(self):
		from yrp.yrp_retail.item_template import guard_policy_merge
		source = self.create_item()
		target_name = create_item_from_template(self.template.name, 'Test Merge '+frappe.generate_hash(length=10), self.group.name, self.hsn.name)
		other_template = frappe.get_doc({'doctype':'YRP Item Master Template',
			'name':'Test Other Template '+frappe.generate_hash(length=10),
			'default_unit_of_measure':self.uom.name}).insert()
		# Represent a link update made after rename loaded its source document.
		frappe.db.set_value('Item',source.name,'yrp_item_master_template',other_template.name)
		with self.assertRaises(frappe.ValidationError):
			guard_policy_merge(source, old=source.name, new=target_name, merge=True)
