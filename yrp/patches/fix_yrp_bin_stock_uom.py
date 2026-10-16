import frappe


def execute():
	"""Reset YRP Bin.stock_uom to the parent Item's stock_uom (variant_of, else the Item itself)."""
	frappe.db.sql(
		"""
		UPDATE `tabYRP Bin` bin
		INNER JOIN `tabItem` item ON item.name = bin.item_code
		INNER JOIN `tabItem` parent ON parent.name = COALESCE(NULLIF(item.variant_of, ''), item.name)
		SET bin.stock_uom = parent.stock_uom
		WHERE COALESCE(bin.stock_uom, '') != COALESCE(parent.stock_uom, '')
		"""
	)
