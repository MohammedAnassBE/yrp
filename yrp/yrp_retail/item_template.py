"""YRP Item Master Template prefill for standard ERPNext Items.

The template Item (has_variants) is the product. A Master Template fills it once,
on creation; the Item owns those values afterwards and its variants inherit them
through native variant propagation. Historical transactions are never rewritten.
"""
import frappe
from frappe import _


def rows_of(rows):
	"""Copy business fields, never child document identity or parent references."""
	return [{field.fieldname: row.get(field.fieldname) for field in row.meta.fields
		if field.fieldtype not in ('Section Break','Column Break','Tab Break','HTML','Button')}
		for row in rows]


def apply_template(item, method=None):
	"""Prefill a new Item from its YRP Item Master Template; the Item owns the values afterwards.

	The link is set only once (Custom Field set_only_once); variants follow their template Item.
	"""
	if item.get('variant_of'):
		if item.get('yrp_item_master_template'):
			frappe.throw(_('Link the YRP Item Master Template on the template Item, not on its variant.'))
		return
	if not item.get('yrp_item_master_template'):
		if item.get('yrp_is_free_item'):
			frappe.throw(_('Configure Free Item on a YRP Item Master Template.'))
		return
	if not item.is_new():
		return
	template = frappe.get_doc('YRP Item Master Template', item.yrp_item_master_template, for_update=True)
	template.check_permission('read')
	template.prefill_item(item)


def validate_template(template):
	"""Reuse ERPNext company-link and duplicate tax-category validation."""
	from erpnext.stock.doctype.item.item import Item, validate_item_default_company_links
	validate_item_default_company_links(template.item_defaults)
	Item.check_item_tax(template)
	companies = [row.company for row in template.item_defaults]
	if len(companies) != len(set(companies)):
		frappe.throw(_('Company defaults may only occur once per Company.'))


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
		if self.get('yrp_item_master_template') or (self.get('variant_of') and frappe.db.get_value(
				'Item', self.variant_of, 'yrp_item_master_template')):
			return
		return super().update_defaults_from_item_group()
