import frappe
from frappe.model.rename_doc import rename_doc

from yrp.attribute_value_identity import MASTER, clear_name_cache, scoped_name

BATCH_SIZE = 100


def execute():
	"""Rename hashed IAV- masters to their value; an identical twin absorbs its duplicate."""
	rows = frappe.get_all(
		MASTER,
		filters={"name": ["like", "IAV-%"]},
		fields=["name", "attribute_name", "attribute_value"],
		order_by="creation asc, name asc",
	)
	renamed, merged, conflicts = 0, 0, []
	for offset in range(0, len(rows), BATCH_SIZE):
		for row in rows[offset : offset + BATCH_SIZE]:
			target, merge = get_target(row)
			if not target:
				conflicts.append(row.name)
				continue
			rename_doc(
				MASTER,
				row.name,
				target,
				force=True,
				merge=merge,
				ignore_permissions=True,
				show_alert=False,
				rebuild_search=False,
			)
			clear_name_cache()
			merged += merge
			renamed += not merge
		frappe.db.commit()
	print(f"Attribute values: renamed {renamed}, merged into identical twins {merged}, conflicts {conflicts}")


def get_target(row):
	"""(name, merge): the value, else its scoped name; merge only into a master of the same pair."""
	for name in (row.attribute_value, scoped_name(row.attribute_name, row.attribute_value)):
		owner = frappe.db.get_value(MASTER, name, ["name", "attribute_name", "attribute_value"], as_dict=True)
		if not owner:
			return name, False
		if (owner.attribute_name, owner.attribute_value) == (row.attribute_name, row.attribute_value):
			return owner.name, True
	return None, False
