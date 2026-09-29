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


def apply_template(item, method=None):
	"""Apply the linked Product's commercial policy, or the direct Template's."""
	old = item.get_doc_before_save()
	if old and old.get('yrp_item_master_template') and not item.get('yrp_item_master_template'):
		frappe.throw(_('An Item managed by a YRP Item Master Template cannot remove its template link.'))
	product_name = item.get('yrp_product')
	if product_name:
		product = frappe.get_doc('YRP Product', product_name, for_update=True)
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
		_source = frappe.get_doc('YRP Item Master Template', item.yrp_item_master_template, for_update=True)
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


def sync_items(template):
	"""Synchronize direct Template Items; Product Items have their own snapshot."""
	old = template.get_doc_before_save()
	if old and (old.is_free_item == template.is_free_item and old.item_type == template.item_type
		and all(rows_of(old.get(field)) == rows_of(template.get(field))
			for field in ('taxes','item_defaults','categories'))):
		return
	# Post-model patches run before Custom Field fixtures are imported on a fresh
	# site. The new Product column may not exist yet during owner repair.
	with_product = frappe.db.has_column('Item', 'yrp_product')
	fields = ['name', 'yrp_product'] if with_product else ['name']
	for row in frappe.db.get_values('Item', filters={'yrp_item_master_template':template.name}, fieldname=fields, as_dict=True, for_update=True, order_by='name'):
		if with_product and row.yrp_product:
			continue
		item = frappe.get_doc('Item', row.name, for_update=True)
		apply_template(item)
		item.save(ignore_permissions=True)


def sync_product_items(product):
	"""Update linked Item masters through validation in the Product transaction."""
	if not frappe.db.has_column('Item', 'yrp_product'):
		return
	old = product.get_doc_before_save()
	if old and all(rows_of(old.get(field)) == rows_of(product.get(field))
			for field in ('taxes', 'item_defaults', 'categories')) and all(
			old.get(field) == product.get(field)
			for field in ('gst_hsn_code', 'is_free_item', 'item_type')):
		return
	for name in frappe.db.get_values('Item', filters={'yrp_product': product.name}, fieldname='name', pluck=True, for_update=True, order_by='name'):
		item = frappe.get_doc('Item', name, for_update=True)
		apply_template(item)
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
