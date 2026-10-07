"""One-time template prefill with fictional company accounts and tax mappings."""
import frappe
from yrp.yrp.doctype.yrp_item_master_template.test_yrp_item_master_template import TestCreateItemFromTemplate
from yrp.yrp.doctype.yrp_item_master_template.yrp_item_master_template import create_item_from_template


class TestTemplateSalesDefaults(TestCreateItemFromTemplate):
	def create_item(self):
		return frappe.get_doc('Item', create_item_from_template(self.template.name, self.item_name, self.group.name, self.hsn.name))

	def test_link_is_set_once_and_free_flag_comes_from_template(self):
		self.template.is_free_item = 1
		self.template.save()
		item = self.create_item()
		self.assertEqual(item.yrp_item_master_template, self.template.name)
		self.assertEqual(item.yrp_is_free_item, 1)
		item.yrp_item_master_template = None
		with self.assertRaises(frappe.ValidationError): item.save()

	def test_existing_item_cannot_gain_a_template_link(self):
		item = frappe.get_doc({'doctype': 'Item', 'item_code': self.item_name, 'item_group': self.group.name,
			'stock_uom': self.uom.name, 'gst_hsn_code': self.hsn.name}).insert()
		item.yrp_item_master_template = self.template.name
		with self.assertRaises(frappe.ValidationError): item.save()

	def test_unlinked_item_cannot_be_free(self):
		item = frappe.get_doc({'doctype': 'Item', 'item_code': self.item_name, 'item_group': self.group.name,
			'stock_uom': self.uom.name, 'gst_hsn_code': self.hsn.name, 'yrp_is_free_item': 1})
		with self.assertRaises(frappe.ValidationError): item.insert()

	def test_linked_template_is_locked(self):
		self.create_item()
		self.template.reload()
		self.template.is_free_item = 1
		with self.assertRaises(frappe.ValidationError): self.template.save()
		self.assertTrue(self.template.has_linked_items)

	def test_prefill_copies_tax_and_company_defaults_once(self):
		suffix = frappe.generate_hash(length=8)
		company = frappe.get_doc({'doctype':'Company', 'company_name':'Test Defaults '+suffix,
			'abbr':'TD'+suffix[:3], 'country':'India', 'default_currency':'INR',
			'chart_of_accounts':'Standard', 'enable_perpetual_inventory':0}).insert()
		income = frappe.db.get_value('Account', {'company':company.name,'root_type':'Income','is_group':0}, 'name')
		expense = frappe.db.get_value('Account', {'company':company.name,'root_type':'Expense','is_group':0}, 'name')
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
		# The Item owns its copy: clearing it is kept on later saves.
		item.set('taxes', [])
		item.save()
		item.reload()
		self.assertFalse(item.taxes)

	def test_classification_is_copied_and_owned_by_the_item(self):
		root, category, value = self.classification()
		self.template.item_type = root.name
		self.template.append('categories', {'category': category.name, 'value': value.name})
		self.template.save()
		item = self.create_item()
		self.assertEqual(item.yrp_item_type, root.name)
		self.assertEqual(item.yrp_categories[0].value, value.name)
		other = self.node('Value', category.name)
		item.yrp_categories[0].value = other.name
		item.save()
		item.reload()
		self.assertEqual(item.yrp_categories[0].value, other.name)

	def test_variants_inherit_classification_and_free_flag(self):
		root, category, value = self.classification()
		item = self.template_item(root, category, value)
		variant = self.variant(item)
		self.assertEqual(variant.yrp_item_type, root.name)
		self.assertEqual(variant.yrp_categories[0].value, value.name)
		self.assertEqual(variant.yrp_is_free_item, 1)
		self.assertFalse(variant.yrp_item_master_template)
		later = self.node('Value', category.name)
		item.reload()
		item.yrp_categories[0].value = later.name
		item.save()
		self.assertEqual(frappe.db.get_value('YRP Item Classification', {'parent': variant.name}, 'value'), later.name)

	def test_variant_price_follows_template_free_policy(self):
		root, category, value = self.classification()
		variant = self.variant(self.template_item(root, category, value))
		prices = frappe.get_doc({'doctype': 'Price List', 'price_list_name': 'Test Variant Prices '+frappe.generate_hash(length=10),
			'enabled': 1, 'selling': 1, 'currency': 'INR'}).insert()
		price = {'doctype': 'Item Price', 'item_code': variant.name, 'price_list': prices.name, 'uom': self.uom.name}
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({**price, 'price_list_rate': 25}).insert()
		self.assertEqual(frappe.get_doc({**price, 'price_list_rate': 0}).insert().price_list_rate, 0)

	def test_linked_template_mappings_are_locked(self):
		root, category, value = self.classification()
		item = self.template_item(root, category, value)
		mapping = frappe.get_doc('YRP Item Item Attribute Mapping', self.template.attributes[0].mapping)
		mapping.set('values', [])
		with self.assertRaises(frappe.ValidationError):
			mapping.save()
		dependent = frappe.get_doc({'doctype': 'YRP Item Dependent Attribute Mapping', 'item': item.name,
			'dependent_attribute': self.attribute, 'details': [{'attribute_value': 'S', 'uom': self.uom.name}],
			'mapping': [{'dependent_attribute_value': 'S', 'depending_attribute': self.attribute}]}).insert()
		frappe.db.set_value('YRP Item Master Template', self.template.name, 'dependent_attribute_mapping', dependent.name)
		with self.assertRaises(frappe.ValidationError):
			dependent.save()
		item_mapping = frappe.get_doc('YRP Item Item Attribute Mapping', item.attributes[0].mapping)
		self.assertNotEqual(item_mapping.name, mapping.name)
		item_mapping.save()

	def test_variant_cannot_link_a_master_template(self):
		root, category, value = self.classification()
		variant = self.variant(self.template_item(root, category, value))
		variant.yrp_item_master_template = self.template.name
		with self.assertRaises(frappe.ValidationError): variant.save()

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
		other = frappe.get_doc({'doctype': 'Item', 'item_code': 'Test Other '+frappe.generate_hash(length=10),
			'item_group': self.group.name, 'stock_uom': self.uom.name, 'gst_hsn_code': self.hsn.name}).insert()
		with self.assertRaises(frappe.ValidationError):
			guard_policy_merge(item, old=item.name, new=other.name, merge=True)
		with self.assertRaises(frappe.ValidationError):
			guard_policy_merge(self.template, old=self.template.name, new='Another Template', merge=True)

	def test_reset_variant_settings_leave_out_the_master_link(self):
		settings = frappe.get_doc('Item Variant Settings')
		settings.set_default_fields()
		self.assertNotIn('yrp_item_master_template', {row.field_name for row in settings.fields})

	def test_patch_drops_master_links_from_variants_and_settings(self):
		from yrp.patches import drop_variant_master_template_links
		variant = self.variant(self.template_item(*self.classification()))
		frappe.db.set_value('Item', variant.name, 'yrp_item_master_template', self.template.name)
		settings = frappe.get_single('Item Variant Settings')
		settings.append('fields', {'field_name': 'yrp_item_master_template'})
		settings.save()
		drop_variant_master_template_links.execute()
		self.assertFalse(frappe.db.get_value('Item', variant.name, 'yrp_item_master_template'))
		self.assertFalse(frappe.db.exists('Variant Field', {'field_name': 'yrp_item_master_template'}))
		frappe.get_doc('Item', variant.name).save()

	def test_merge_compares_the_effective_template_policy(self):
		from yrp.yrp_retail.item_template import guard_policy_merge
		free_variant = self.variant(self.template_item(*self.classification()))
		plain, other_plain = (frappe.get_doc({'doctype': 'Item', 'item_code': 'Test Plain '+frappe.generate_hash(length=10),
			'item_group': self.group.name, 'stock_uom': self.uom.name, 'gst_hsn_code': self.hsn.name}).insert() for _ in range(2))
		with self.assertRaises(frappe.ValidationError):
			guard_policy_merge(free_variant, old=free_variant.name, new=plain.name, merge=True)
		guard_policy_merge(plain, old=plain.name, new=other_plain.name, merge=True)

	def node(self, kind, parent=None):
		return frappe.get_doc({'doctype':'YRP Item Category','category_name':'Test '+kind+' '+frappe.generate_hash(length=8),
			'node_type':kind,'is_group':int(kind!='Value'),'parent_yrp_item_category':parent}).insert()

	def classification(self):
		root = self.node('Item Type')
		category = self.node('Category', root.name)
		return root, category, self.node('Value', category.name)

	def template_item(self, root, category, value):
		attribute = frappe.get_doc({'doctype': 'Item Attribute', 'attribute_name': 'Test Size '+frappe.generate_hash(length=8),
			'item_attribute_values': [{'attribute_value': 'S', 'abbr': 'S'}]}).insert()
		self.template.update({'item_type': root.name, 'is_free_item': 1, 'attributes': [{'attribute': attribute.name}]})
		self.template.append('categories', {'category': category.name, 'value': value.name})
		self.template.save()
		mapping = frappe.get_doc('YRP Item Item Attribute Mapping', self.template.attributes[0].mapping)
		mapping.append('values', {'attribute_value': 'S'})
		mapping.save()
		self.attribute = attribute.name
		item = self.create_item()
		self.assertTrue(item.has_variants)
		return item

	def variant(self, item):
		from yrp.yrp.doctype.yrp_item.yrp_item import get_or_create_variant
		return frappe.get_doc('Item', get_or_create_variant(item.name, {self.attribute: 'S'}))
