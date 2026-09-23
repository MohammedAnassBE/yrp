"""Private attribute-value snapshots for Item Master Templates and Products.

Only mappings detached or consumed by a successful owner save are candidates
for cleanup. Native Item, production and other live references keep their maps;
unrelated standalone mappings are never swept. All work shares the owner's
transaction, including native deletion/link checks.
"""

import frappe
from frappe import _

MAPPING = "YRP Item Item Attribute Mapping"
REFERENCE_TABLES = (
	"YRP Item Item Attribute",
	"Item Variant Attribute",
	"YRP IPD Item Attribute",
)


def mapping_names(doc):
	return {row.mapping for row in doc.get("attributes") or [] if row.mapping} if doc else set()


def has_other_references(name, owner=None):
	"""Check the actual shared child tables, including manufacturing parents."""
	for table in REFERENCE_TABLES:
		if not frappe.get_meta(table).has_field("mapping"):
			continue
		for row in frappe.get_all(table, filters={"mapping": name}, fields=["parenttype", "parent"]):
			if not owner or (row.parenttype, row.parent) != (owner.doctype, owner.name):
				return True
	return False


def ensure_owned_mappings(doc):
	"""Clone incoming/shared mappings; retain private mappings on ordinary saves."""
	previous = doc.get_doc_before_save()
	old_names = mapping_names(previous)
	current_names = mapping_names(doc)
	doc.flags.yrp_consumed_attribute_mappings = set()
	# Lock old and replacement IDs together, in one consistent order. Do not
	# lock other owners/child rows while this owner is already locked.
	for name in sorted(old_names | current_names):
		frappe.db.get_value(MAPPING, name, "name", for_update=True)
	copies = {}
	for row in doc.get("attributes") or []:
		if not row.mapping:
			mapping = frappe.get_doc({"doctype": MAPPING, "attribute_name": row.attribute}).insert()
			row.mapping = mapping.name
			continue
		source = frappe.get_doc(MAPPING, row.mapping, for_update=True)
		if source.attribute_name != row.attribute:
			frappe.throw(_("Attribute mapping must belong to the selected attribute."))
		if source.name in old_names and not has_other_references(source.name, doc):
			continue
		if source.name not in copies:
			source.check_permission("read")
			copies[source.name] = frappe.copy_doc(source).insert().name
			doc.flags.yrp_consumed_attribute_mappings.add(source.name)
		row.mapping = copies[source.name]


def cleanup_owner_mappings(doc, *, deleted=False):
	"""Delete only former/consumed mappings after the owner's rows are persisted."""
	if deleted:
		candidates = mapping_names(doc)
	else:
		candidates = mapping_names(doc.get_doc_before_save())
		candidates.update(doc.flags.get("yrp_consumed_attribute_mappings") or ())
		candidates.difference_update(mapping_names(doc))
	for name in sorted(candidates):
		if not frappe.db.get_value(MAPPING, name, "name", for_update=True):
			continue
		if has_other_references(name):
			continue
		try:
			# This is a cascade from the authorized owner operation. Keep native
			# link protection for custom or dynamic references outside our tables.
			frappe.delete_doc(MAPPING, name, ignore_permissions=True)
		except frappe.LinkExistsError:
			frappe.clear_last_message()
	doc.flags.pop("yrp_consumed_attribute_mappings", None)
