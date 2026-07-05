import frappe


def run():
	# Check Item
	print("ITEM: Mens Sports Vest - 11225 ->", "OK" if frappe.db.exists("Item", "Mens Sports Vest - 11225") else "MISSING")

	# Check attributes
	for a in ["Stage", "Panel", "Colour", "Size"]:
		print(f"ATTRIBUTE {a}:", "OK" if frappe.db.exists("Item Attribute", a) else "MISSING")

	# Check stage values
	for v in ["Cut", "Piece", "Pack", "Greige"]:
		print(f"VALUE {v}:", "OK" if frappe.db.exists("Item Attribute Value", v) else "MISSING")

	# Check processes
	for p in ["Cutting", "Stitching", "Packing", "Yolk Fusing", "Tower Fusing", "Chest Fusing", "Ironing"]:
		print(f"PROCESS {p}:", "OK" if frappe.db.exists("Process", p) else "MISSING")

	# Check accessory items
	for i in [
		"Fusing Sticker-MoveAir",
		"Tower Fusing Sticker-MoveAir",
		"Essdee Premium Vest Fusing Badge",
		"Essdee Printed Inner Card Double side Coating 400 GSM",
		"Essdee Premium Sports Vest 11218 Slider Bopp",
		"Essdee Mens Sports Vest - 11225 Top Box",
		"Essdee Mens Sports Vest - 11225 Bottom Box",
	]:
		print(f"BOM ITEM {i}:", "OK" if frappe.db.exists("Item", i) else "MISSING")

	# Check UOMs
	for u in ["Nos", "kg"]:
		print(f"UOM {u}:", "OK" if frappe.db.exists("UOM", u) else "MISSING")
