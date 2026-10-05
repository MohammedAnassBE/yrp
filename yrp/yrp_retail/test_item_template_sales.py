"""Native template inheritance with fictional company accounts and tax mappings."""
import frappe
from unittest.mock import patch
from yrp.yrp_retail.item_template import sync_product_items_job, sync_template_items_job
from yrp.yrp.doctype.yrp_item_master_template.test_yrp_item_master_template import TestCreateItemFromTemplate
from yrp.yrp.doctype.yrp_item_master_template.yrp_item_master_template import create_item_from_template


class TestTemplateSalesDefaults(TestCreateItemFromTemplate):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.db.after_commit.reset)

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
		sync_template_items_job(self.template.name)
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
		sync_template_items_job(self.template.name)
		item.reload()
		self.assertFalse(item.taxes)
		self.assertFalse(item.item_defaults[0].expense_account)
		self.assertEqual(item.item_defaults[0].income_account,income)
		self.template.set('item_defaults',[])
		self.template.save()
		sync_template_items_job(self.template.name)
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

	def make_product(self):
		from yrp.yrp_retail.doctype.yrp_product.yrp_product import get_template_defaults
		return frappe.get_doc(dict(doctype='YRP Product', product_name='Test Product '+frappe.generate_hash(length=8),
			item_template=self.template.name, gst_hsn_code=self.hsn.name, **get_template_defaults(self.template.name))).insert()

	def make_product_item(self, product):
		item = frappe.get_doc('Item', create_item_from_template(
			self.template.name, 'Test Product Item '+frappe.generate_hash(length=10), self.group.name, self.hsn.name))
		item.yrp_product = product.name
		item.save()
		return item

	def test_product_change_enqueues_one_deduplicated_job(self):
		product = self.make_product()
		with patch('frappe.enqueue') as enqueue:
			product.is_free_item = 1
			product.save()
			enqueue.assert_not_called()
			frappe.db.after_commit.run()
		enqueue.assert_called_once()
		args, kwargs = enqueue.call_args
		self.assertEqual(args[0], 'yrp.yrp_retail.item_template.sync_product_items_job')
		self.assertEqual(kwargs['job_id'], f'yrp-yrp-product-item-sync::{product.name}')
		self.assertTrue(kwargs['deduplicate'])
		self.assertEqual(kwargs['queue'], 'long')
		self.assertEqual(kwargs['name'], product.name)

	def enqueued_job_id(self, product, statuses):
		from rq.job import JobStatus
		base = f'yrp-yrp-product-item-sync::{product.name}'
		lookup = lambda job_id: {base: statuses[0], base + '::followup': statuses[1]}[job_id]
		with patch('frappe.enqueue') as enqueue, patch('frappe.utils.background_jobs.get_job_status', side_effect=lookup):
			product.is_free_item = 0 if product.is_free_item else 1
			product.save()
			frappe.db.after_commit.run()
		self.assertTrue(enqueue.call_args.kwargs['deduplicate'])
		return enqueue.call_args.kwargs['job_id'].removeprefix(base)

	def test_job_ids_alternate_while_a_job_is_running(self):
		from rq.job import JobStatus
		product = self.make_product()
		self.assertEqual(self.enqueued_job_id(product, (None, None)), '')
		self.assertEqual(self.enqueued_job_id(product, (JobStatus.QUEUED, None)), '')
		self.assertEqual(self.enqueued_job_id(product, (JobStatus.STARTED, None)), '::followup')
		self.assertEqual(self.enqueued_job_id(product, (JobStatus.QUEUED, JobStatus.STARTED)), '')

	def test_unchanged_product_save_enqueues_nothing(self):
		product = self.make_product()
		with patch('frappe.enqueue') as enqueue:
			product.save()
			frappe.db.after_commit.run()
		enqueue.assert_not_called()

	def test_template_change_enqueues_deduplicated_job(self):
		with patch('frappe.enqueue') as enqueue:
			self.template.is_free_item = 1
			self.template.save()
			frappe.db.after_commit.run()
			enqueue.assert_called_once()
			self.assertEqual(enqueue.call_args.kwargs['job_id'], f'yrp-yrp-item-master-template-item-sync::{self.template.name}')
			enqueue.reset_mock()
			self.template.save()
			frappe.db.after_commit.run()
			enqueue.assert_not_called()

	def test_product_job_syncs_items_and_skips_those_in_sync(self):
		product = self.make_product()
		item = self.make_product_item(product)
		product.is_free_item = 1
		product.save()
		self.assertEqual(frappe.db.get_value('Item', item.name, 'yrp_is_free_item'), 0)
		sync_product_items_job(product.name)
		self.assertEqual(frappe.db.get_value('Item', item.name, 'yrp_is_free_item'), 1)
		with patch('frappe.model.document.Document.save') as save:
			sync_product_items_job(product.name)
		save.assert_not_called()

	def test_job_logs_failed_item_continues_and_raises(self):
		product = self.make_product()
		bad = self.make_product_item(product)
		good = self.make_product_item(product)
		product.is_free_item = 1
		product.save()
		real = frappe.get_doc

		def fail_for_bad(doctype, name=None, *args, **kwargs):
			if doctype == 'Item' and name == bad.name:
				raise frappe.ValidationError('boom')
			return real(doctype, name, *args, **kwargs)

		with patch('frappe.get_doc', side_effect=fail_for_bad), patch('frappe.log_error') as log_error, \
				patch.object(frappe.db, 'commit'):
			with self.assertRaises(frappe.ValidationError):
				sync_product_items_job(product.name)
		log_error.assert_called_once()
		self.assertIn(bad.name, log_error.call_args.kwargs['title'])
		self.assertEqual(frappe.db.get_value('Item', good.name, 'yrp_is_free_item'), 1)
		self.assertEqual(frappe.db.get_value('Item', bad.name, 'yrp_is_free_item'), 0)
