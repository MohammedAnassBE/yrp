import frappe


def execute():
	"""Supplier's single `terms_and_condition` link was split into PO and WO
	variants (`po_terms_and_condition` / `wo_terms_and_condition`). Carry the
	old value into the PO field for existing suppliers, then drop the orphan
	column. WO starts empty and falls through the default-term cascade.

	Follows the copy-then-drop safety pattern of `drop_received_type_role_flags.py`:
	never drop the source column unless the destination exists (else data loss),
	commit the copy before the DDL, and clear the doctype cache afterwards.
	"""
	if not frappe.db.has_column("Supplier", "terms_and_condition"):
		return

	# Never drop the source unless the copy destination exists — otherwise the
	# data move is skipped and the DROP would discard it.
	if not frappe.db.has_column("Supplier", "po_terms_and_condition"):
		return

	frappe.db.sql(
		"""
		UPDATE `tabSupplier`
		SET po_terms_and_condition = terms_and_condition
		WHERE COALESCE(po_terms_and_condition, '') = ''
		  AND COALESCE(terms_and_condition, '') != ''
		"""
	)
	frappe.db.commit()

	frappe.db.sql_ddl("ALTER TABLE `tabSupplier` DROP COLUMN `terms_and_condition`")
	frappe.clear_cache(doctype="Supplier")
