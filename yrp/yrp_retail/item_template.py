"""Template-owned sales defaults on standard ERPNext Items.

Only explicitly linked Items are synchronized. Native tax rows and company-wise
Item Defaults retain ERPNext's accounting/tax behavior; historical transactions
are never rewritten when a template changes.
"""
import frappe
from frappe import _


def rows_of(rows):
	"""Copy business fields, never child document identity or parent references."""
	return [{field.fieldname: row.get(field.fieldname) for field in row.meta.fields
		if field.fieldtype not in ('Section Break','Column Break','Tab Break','HTML','Button')}
		for row in rows]


def apply_template(item, method=None):
	"""Keep managed defaults authoritative before ERPNext Item validation."""
	old = item.get_doc_before_save()
	if old and old.get('yrp_item_master_template') and not item.get('yrp_item_master_template'):
		frappe.throw(_('An Item managed by a YRP Item Master Template cannot remove its template link.'))
	if not item.get('yrp_item_master_template'):
		if item.get('yrp_is_free_item'):
			frappe.throw(_('Configure Free Item on a YRP Item Master Template.'))
		return
	template = frappe.get_doc('YRP Item Master Template', item.yrp_item_master_template, for_update=True)
	if not old or old.get('yrp_item_master_template') != item.yrp_item_master_template:
		template.check_permission('read')
	item.yrp_is_free_item = template.is_free_item
	item.yrp_item_type = template.item_type
	item.set('yrp_categories', rows_of(template.categories))
	item.set('taxes', rows_of(template.taxes))
	item.set('item_defaults', rows_of(template.item_defaults))


def validate_template(template):
	"""Reuse ERPNext company-link and duplicate tax-category validation."""
	from erpnext.stock.doctype.item.item import Item, validate_item_default_company_links
	validate_item_default_company_links(template.item_defaults)
	Item.check_item_tax(template)
	companies = [row.company for row in template.item_defaults]
	if len(companies) != len(set(companies)):
		frappe.throw(_('Company defaults may only occur once per Company.'))


def sync_items(template):
	"""Synchronize linked Items using normal saves in the template transaction."""
	old = template.get_doc_before_save()
	if old and (old.is_free_item == template.is_free_item and old.item_type == template.item_type
		and all(rows_of(old.get(field)) == rows_of(template.get(field))
			for field in ('taxes','item_defaults','categories'))):
		return
	for name in frappe.db.get_values('Item', filters={'yrp_item_master_template':template.name}, fieldname='name', pluck=True, for_update=True, order_by='name'):
		item = frappe.get_doc('Item', name, for_update=True)
		apply_template(item)
		item.save(ignore_permissions=True)


def setup_item_sales():
	"""Install idempotent standard Item links for fresh sites and migrations."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
	create_custom_fields({'Item':[
		{'fieldname':'yrp_item_master_template','fieldtype':'Link','label':'YRP Item Master Template','options':'YRP Item Master Template','module':'YRP Retail'},
		{'fieldname':'yrp_is_free_item','fieldtype':'Check','label':'Free Item','read_only':1,'module':'YRP Retail'},
		{'fieldname':'yrp_item_type','fieldtype':'Link','label':'YRP Item Type','options':'YRP Item Category','module':'YRP Retail'},
		{'fieldname':'yrp_categories','fieldtype':'Table','label':'YRP Categories','options':'YRP Item Classification','module':'YRP Retail'},
	]})


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


class ItemSalesTemplateMixin:
	"""Prevent Item Group fallback from replacing explicitly managed defaults."""
	def update_defaults_from_item_group(self):
		if self.get('yrp_item_master_template'):
			return
		return super().update_defaults_from_item_group()
