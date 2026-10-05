"""Template- or Product-owned sales defaults on standard ERPNext Items.

One Product may cover multiple Item variants. Product-specific commercial
defaults take precedence over the parent template. Native tax rows and Item
Defaults retain ERPNext behavior; historical transactions are never rewritten.
"""
import frappe
from frappe import _


def rows_of(rows):
	"""Copy business fields, never child document identity or parent references."""
	return [{field.fieldname: row.get(field.fieldname) for field in row.meta.fields
		if field.fieldtype not in ('Section Break','Column Break','Tab Break','HTML','Button')}
		for row in rows]


def apply_template(item, method=None, source=None):
	"""Apply the linked Product's commercial policy, or the direct Template's.

	`source` is an already loaded Product (or Template) from a sync job.
	"""
	old = item.get_doc_before_save()
	if old and old.get('yrp_item_master_template') and not item.get('yrp_item_master_template'):
		frappe.throw(_('An Item managed by a YRP Item Master Template cannot remove its template link.'))
	product_name = item.get('yrp_product')
	if product_name:
		product = source if source and source.doctype == 'YRP Product' else frappe.get_doc('YRP Product', product_name, for_update=True)
		if not old or old.get('yrp_product') != product_name:
			product.check_permission('read')
			if product.disabled:
				frappe.throw(_('A disabled YRP Product cannot be linked to a new Item.'))
		if item.get('yrp_item_master_template') and item.yrp_item_master_template != product.item_template:
			frappe.throw(_('The Item Master Template must match the linked YRP Product.'))
		item.yrp_item_master_template = product.item_template
		_source = product
	else:
		_source = None
	if not item.get('yrp_item_master_template'):
		if item.get('yrp_is_free_item'):
			frappe.throw(_('Configure Free Item on a YRP Item Master Template.'))
		return
	if not _source:
		_source = source or frappe.get_doc('YRP Item Master Template', item.yrp_item_master_template, for_update=True)
		if not old or old.get('yrp_item_master_template') != item.yrp_item_master_template:
			_source.check_permission('read')
	item.yrp_is_free_item = _source.is_free_item
	item.yrp_item_type = _source.item_type
	item.set('yrp_categories', rows_of(_source.categories))
	item.set('taxes', rows_of(_source.taxes))
	item.set('item_defaults', rows_of(_source.item_defaults))
	if product_name and item.meta.has_field('gst_hsn_code'):
		item.gst_hsn_code = _source.gst_hsn_code


def validate_template(template):
	"""Reuse ERPNext company-link and duplicate tax-category validation."""
	from erpnext.stock.doctype.item.item import Item, validate_item_default_company_links
	validate_item_default_company_links(template.item_defaults)
	Item.check_item_tax(template)
	companies = [row.company for row in template.item_defaults]
	if len(companies) != len(set(companies)):
		frappe.throw(_('Company defaults may only occur once per Company.'))


ITEM_TABLES = ('taxes', 'item_defaults', 'yrp_categories')
SOURCE_TABLES = ('taxes', 'item_defaults', 'categories')
SYNCED_FIELDS = ('yrp_item_master_template', 'yrp_is_free_item', 'yrp_item_type', 'gst_hsn_code')


def get_synced_snapshot(item):
	"""Item values owned by the Template or Product policy."""
	return ([item.get(field) for field in SYNCED_FIELDS],
		[rows_of(item.get(table)) for table in ITEM_TABLES])


def enqueue_item_sync(job, doc):
	"""After commit, queue one deduplicated job per Product or Template.

	A running job already loaded older values, so alternate between the base and
	follow-up ids: the next save always lands on a job that has not started.
	"""
	from rq.job import JobStatus
	from frappe.utils.background_jobs import get_job_status

	base_id = f'yrp-{doc.doctype.lower().replace(" ", "-")}-item-sync::{doc.name}'
	name = doc.name

	def enqueue():
		is_running = get_job_status(base_id) == JobStatus.STARTED
		job_id = f'{base_id}::followup' if is_running and get_job_status(f'{base_id}::followup') != JobStatus.STARTED else base_id
		frappe.enqueue(
			f'yrp.yrp_retail.item_template.{job}', queue='long', timeout=1800,
			job_id=job_id, deduplicate=True, name=name,
		)

	frappe.db.after_commit.add(enqueue)


def sync_items(template):
	"""Queue direct Template Item sync when the Template policy changed."""
	old = template.get_doc_before_save()
	if not old or (old.is_free_item == template.is_free_item and old.item_type == template.item_type
		and all(rows_of(old.get(field)) == rows_of(template.get(field)) for field in SOURCE_TABLES)):
		return
	enqueue_item_sync('sync_template_items_job', template)


def sync_product_items(product):
	"""Queue linked Item sync when the Product policy changed."""
	if not frappe.db.has_column('Item', 'yrp_product'):
		return
	old = product.get_doc_before_save()
	if not old or all(rows_of(old.get(field)) == rows_of(product.get(field)) for field in SOURCE_TABLES) and all(
			old.get(field) == product.get(field) for field in ('gst_hsn_code', 'is_free_item', 'item_type')):
		return
	enqueue_item_sync('sync_product_items_job', product)


def sync_template_items_job(name):
	"""Background: sync direct Template Items, skipping Product-owned ones."""
	template = frappe.get_doc('YRP Item Master Template', name, for_update=True)
	with_product = frappe.db.has_column('Item', 'yrp_product')
	rows = frappe.db.get_values('Item', filters={'yrp_item_master_template': name},
		fieldname=['name', 'yrp_product'] if with_product else ['name'], as_dict=True, order_by='name')
	sync_item_names(template, [row.name for row in rows if not (with_product and row.yrp_product)])


def sync_product_items_job(name):
	"""Background: sync every Item linked to the Product."""
	product = frappe.get_doc('YRP Product', name, for_update=True)
	sync_item_names(product, frappe.db.get_all('Item', filters={'yrp_product': name}, pluck='name', order_by='name'))


def sync_item_names(source, item_names):
	"""Apply source to each Item; log and continue past failures, then raise."""
	failed = []
	for item_name in item_names:
		savepoint = 'yrp_item_sync'
		frappe.db.savepoint(savepoint)
		try:
			sync_item(source, item_name)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			failed.append(item_name)
			frappe.log_error(title=f'YRP item sync failed: {source.doctype} {source.name} / Item {item_name}')
	if failed:
		frappe.db.commit()  # raising rolls back the job, so keep the good items first
		frappe.throw(_('Item sync for {0} failed for: {1}').format(source.name, ', '.join(failed)))


def sync_item(source, item_name):
	"""Save the Item only when its owned values differ from the source."""
	item = frappe.get_doc('Item', item_name, for_update=True)
	before = get_synced_snapshot(item)
	apply_template(item, source=source)
	if get_synced_snapshot(item) != before:
		item.save(ignore_permissions=True)


def guard_policy_merge(doc, method=None, old=None, new=None, merge=False, **kwargs):
	"""Native merge rewrites links without Item validation; preserve policy identity."""
	if not merge:
		return
	from yrp.yrp_retail.pricing import lock_pricing_policy
	lock_pricing_policy()
	if doc.doctype == 'YRP Item Master Template':
		frappe.throw(_('Item Master Template merging requires an explicit inheritance migration.'))
	if doc.doctype == 'Item':
		source = frappe.get_doc('Item', old or doc.name, for_update=True)
		target = frappe.get_doc('Item', new, for_update=True)
		if source.get('yrp_item_master_template') != target.get('yrp_item_master_template'):
			frappe.throw(_('Items with different YRP Item Master Templates cannot be merged.'))
		if source.get('yrp_product') != target.get('yrp_product'):
			frappe.throw(_('Items with different YRP Products cannot be merged.'))


class ItemSalesTemplateMixin:
	"""Prevent Item Group fallback from replacing explicitly managed defaults."""
	def update_defaults_from_item_group(self):
		if self.get('yrp_item_master_template'):
			return
		return super().update_defaults_from_item_group()
