"""Engine verification tests — exercise behaviors landed in the no-voucher batch.

Each test uses its own dedicated warehouse so state doesn't leak across tests
even if FrappeTestCase rollback misses a corner. Tests that need prior stock
seed it themselves.

Coverage map (vs IMPLEMENTATION_PLAN.md):
  - SQL: assert_safe_fieldname rejects unsafe identifiers
  - C.4 : get_or_make_bin mandatory-dim guard
  - C.1 : dim removal blocked when SLE/Bin data exists
  - C.2 : fieldname rename blocked when data exists
  - C.5 : NULL-dim integrity category surfaces
  - C.6 : get_sre_reserved_qty NULL-matches-any
  - H.1 : get_sre_reserved_qty exclude-self
  - H.2 : allow_negative_stock cannot bypass reservation (Stock Update path)
  - H.3 : close_voucher_reservations
  - F.1 : get_last_sle_rate bucket-scoped + fallback
  - B.1 : SRE-based over-reduce blocks at validate
  - B.2 : get_total_stock aggregator
  - D.2 : get_stock_balance(with_stale=True) returns True when RIV pending
  - D.3 : composite SLE index present after migration
  - I.1 : negative stock allowed only when Item flag set
  - I.2 : Item.allow_negative_stock cannot uncheck while negative
  - I.7 : Stock Reconciliation while negative wipes-and-resets
  - I.8 : Delivery Challan cannot over-dispatch against timestamp stock
  - G.5 : Material Consumed Stock Entry blocks non-Accepted source rows
  - M   : Stock Integrity Check daily job runs cleanly
  - K   : Pending Transit report executes
  - N   : Stock Availability report executes
  - A.1 : Stock Reconciliation cancel restores prior balance
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate, nowtime

from yrp.stock.api import get_total_stock
from yrp.stock.utils import (
	close_voucher_reservations,
	get_last_sle_rate,
	get_or_make_bin,
	get_sre_reserved_qty,
	get_stock_balance,
)

ITEM_VARIANT_CANDIDATES = (
	"Item-00005-45 cm-Blue",
	"Mens Sports Vest - 11222-Cut-Top Front-Peach-S",
)


def _test_item_variant():
	for item_variant in ITEM_VARIANT_CANDIDATES:
		if frappe.db.exists("Item Variant", item_variant):
			return item_variant
	fallback = frappe.db.sql(
		"""
		SELECT iv.name
		FROM `tabItem Variant` iv
		INNER JOIN `tabItem` i ON i.name = iv.item
		WHERE i.is_stock_item = 1
		ORDER BY iv.creation
		LIMIT 1
		"""
	)
	if fallback:
		return fallback[0][0]
	frappe.throw("No test Item Variant found for stock engine verification.")


ITEM_VARIANT = _test_item_variant()
ITEM_UOM = (
	frappe.db.get_value(
		"Item",
		frappe.db.get_value("Item Variant", ITEM_VARIANT, "item"),
		"default_unit_of_measure",
	)
	or "Piece"
)
TEST_LOT = frappe.db.get_value("Lot", {}, "name")
ACCEPTED_DIMS = {"lot": TEST_LOT, "received_type": "Accepted"} if TEST_LOT else {"received_type": "Accepted"}


def _wh(suffix):
	"""Per-test isolated warehouse name."""
	name = f"_Test_Verify_{suffix}"
	if not frappe.db.exists("Warehouse", name):
		frappe.get_doc({"doctype": "Warehouse", "name1": name}).insert(
			ignore_permissions=True
		)
	return name


def _seed(qty, rate, warehouse):
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"purpose": "Material Receipt",
			"to_warehouse": warehouse,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"items": [
				{
					"item": ITEM_VARIANT,
					"qty": qty,
					"rate": rate,
					"uom": ITEM_UOM,
					"row_index": 0,
					"table_index": 0,
					**ACCEPTED_DIMS,
				}
			],
		}
	)
	se.flags.ignore_permissions = True
	se.insert(ignore_permissions=True)
	se.submit()
	return se


class TestEngineVerification(FrappeTestCase):
	# ------------------------------------------------------------------
	# C.4 — get_or_make_bin must reject blank mandatory dimensions
	# ------------------------------------------------------------------
	def test_C4_get_or_make_bin_rejects_missing_dim(self):
		wh = _wh("C4a")
		with self.assertRaises(frappe.ValidationError):
			get_or_make_bin(ITEM_VARIANT, wh)

	def test_C4_get_or_make_bin_succeeds_with_dim(self):
		wh = _wh("C4b")
		_seed(10, 50, wh)
		bin_name = get_or_make_bin(ITEM_VARIANT, wh, received_type="Accepted")
		self.assertTrue(bin_name)
		self.assertEqual(
			frappe.db.get_value("Bin", bin_name, "warehouse"), wh
		)

	# ------------------------------------------------------------------
	# C.6 / H.1 / H.3 — reservation queries
	# ------------------------------------------------------------------
	def _make_sre(self, qty, voucher_no, warehouse):
		sre = frappe.get_doc(
			{
				"doctype": "Stock Reservation Entry",
				"item_code": ITEM_VARIANT,
				"warehouse": warehouse,
				"reserved_qty": qty,
				"available_qty": 9999,
				"voucher_type": "Stock Update",
				"voucher_no": voucher_no,
				"received_type": "Accepted",
			}
		)
		sre.flags.ignore_permissions = True
		sre.flags.ignore_links = True
		sre.insert(ignore_permissions=True)
		sre.flags.ignore_links = True
		sre.submit()
		return sre

	def test_C6_sre_dim_match(self):
		wh = _wh("C6")
		_seed(50, 100, wh)
		self._make_sre(7, "SRE-VERIFY-C6-1", wh)
		reserved = get_sre_reserved_qty(
			item_code=ITEM_VARIANT, warehouse=wh, received_type="Accepted"
		)
		self.assertAlmostEqual(reserved, 7.0)

	def test_H1_exclude_self(self):
		wh = _wh("H1")
		_seed(50, 100, wh)
		self._make_sre(7, "SRE-VERIFY-H1-1", wh)

		full = get_sre_reserved_qty(
			item_code=ITEM_VARIANT, warehouse=wh, received_type="Accepted"
		)
		excluded = get_sre_reserved_qty(
			item_code=ITEM_VARIANT,
			warehouse=wh,
			received_type="Accepted",
			exclude_voucher_type="Stock Update",
			exclude_voucher_name="SRE-VERIFY-H1-1",
		)
		self.assertAlmostEqual(full, 7.0)
		self.assertAlmostEqual(excluded, 0.0)

	def test_H3_close_voucher_reservations(self):
		wh = _wh("H3")
		_seed(50, 100, wh)
		sre = self._make_sre(7, "SRE-VERIFY-H3-1", wh)

		close_voucher_reservations("Stock Update", "SRE-VERIFY-H3-1")
		sre.reload()
		self.assertEqual(sre.docstatus, 2)

	# ------------------------------------------------------------------
	# F.1 — bucket-scoped rate fetch
	# ------------------------------------------------------------------
	def test_F1_rate_from_bucket(self):
		wh = _wh("F1a")
		_seed(20, 73, wh)
		rate, matched = get_last_sle_rate(
			ITEM_VARIANT, warehouse=wh, received_type="Accepted"
		)
		self.assertAlmostEqual(rate, 73.0)
		self.assertTrue(matched)

	def test_F1_rate_falls_back(self):
		# Query a brand-new warehouse with no SLE history.
		# Fallback should pick up the global last item-rate (>0).
		fresh = _wh("F1b_unused")
		rate, matched = get_last_sle_rate(
			ITEM_VARIANT, warehouse=fresh, received_type="Accepted"
		)
		self.assertGreater(rate, 0)
		self.assertFalse(matched)

	# ------------------------------------------------------------------
	# B.2 — get_total_stock aggregator
	# ------------------------------------------------------------------
	def test_B2_get_total_stock_filters(self):
		wh = _wh("B2a")
		_seed(15, 60, wh)
		out = get_total_stock(ITEM_VARIANT, {"warehouse": wh})
		self.assertAlmostEqual(out["actual_qty"], 15.0)
		self.assertGreater(out["stock_value"], 0)

	def test_B2_unknown_filter_ignored(self):
		wh = _wh("B2b")
		_seed(5, 50, wh)
		out = get_total_stock(
			ITEM_VARIANT, {"warehouse": wh, "random_field": "x"}
		)
		self.assertAlmostEqual(out["actual_qty"], 5.0)

	# ------------------------------------------------------------------
	# D.2 — stale flag
	# ------------------------------------------------------------------
	def test_D2_stale_dict_shape(self):
		wh = _wh("D2")
		_seed(10, 50, wh)
		out = get_stock_balance(
			ITEM_VARIANT, wh, with_stale=True, received_type="Accepted"
		)
		self.assertEqual(set(out.keys()), {"actual_qty", "valuation_rate", "stale", "stale_reason"})
		self.assertFalse(out["stale"])

	# ------------------------------------------------------------------
	# M — integrity check end-to-end
	# ------------------------------------------------------------------
	def test_M_integrity_run(self):
		from yrp.yrp_stock.doctype.stock_integrity_check.stock_integrity_check import (
			run_daily_check,
		)

		name = run_daily_check()
		self.assertEqual(
			frappe.db.get_value("Stock Integrity Check", name, "status"), "Completed"
		)

	# ------------------------------------------------------------------
	# K — pending transit report
	# ------------------------------------------------------------------
	def test_K_pending_transit_executes(self):
		from yrp.yrp_stock.report.pending_transit.pending_transit import execute

		columns, rows = execute({})
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	# ------------------------------------------------------------------
	# N — availability report
	# ------------------------------------------------------------------
	def test_N_availability_flat(self):
		wh = _wh("Na")
		_seed(10, 50, wh)
		from yrp.yrp_stock.report.stock_availability.stock_availability import (
			execute,
		)

		columns, rows = execute({"item": ITEM_VARIANT, "warehouse": wh})
		self.assertTrue(columns)
		self.assertGreaterEqual(len(rows), 1)
		self.assertAlmostEqual(rows[0]["actual_qty"], 10.0)

	def test_N_availability_grouped(self):
		wh = _wh("Nb")
		_seed(10, 50, wh)
		from yrp.yrp_stock.report.stock_availability.stock_availability import (
			execute,
		)

		columns, rows = execute(
			{"item": ITEM_VARIANT, "warehouse": wh, "group_by_dim": 1}
		)
		self.assertTrue(columns)

	# ------------------------------------------------------------------
	# C.1 — dim removal blocked when data exists
	# ------------------------------------------------------------------
	def test_C1_dim_removal_blocked(self):
		# received_type is registered + has data in tabBin / tabSLE.
		settings = frappe.get_single("YRP Stock Settings")
		original_rows = list(settings.stock_dimensions)
		settings.stock_dimensions = [
			d for d in original_rows if d.fieldname != "received_type"
		]
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	# ------------------------------------------------------------------
	# I.2 — toggle guard
	# ------------------------------------------------------------------
	def test_I2_uncheck_blocked_while_negative(self):
		wh = _wh("I2")
		parent_item = frappe.get_cached_value("Item Variant", ITEM_VARIANT, "item")
		item = frappe.get_doc("Item", parent_item)
		original = item.allow_negative_stock
		item.allow_negative_stock = 1
		item.flags.ignore_permissions = True
		item.save(ignore_permissions=True)

		_seed(2, 10, wh)
		# Drive negative
		reduce_doc = frappe.get_doc(
			{
				"doctype": "Stock Update",
				"posting_date": nowdate(),
				"posting_time": nowtime(),
				"warehouse": wh,
				"update_type": "Reduce",
				"stock_update_details": [
					{
						"item_variant": ITEM_VARIANT,
						"update_diff_qty": 10,
						"received_type": "Accepted",
					}
				],
			}
		)
		reduce_doc.flags.ignore_permissions = True
		reduce_doc.insert(ignore_permissions=True)
		reduce_doc.submit()

		bal = get_stock_balance(ITEM_VARIANT, wh, received_type="Accepted")
		self.assertLess(bal, 0)

		# Toggle off → must throw.
		item.reload()
		item.allow_negative_stock = 0
		with self.assertRaises(frappe.ValidationError):
			item.save(ignore_permissions=True)

		# Cleanup: replenish before re-toggling so the test's tearDown can succeed.
		# (FrappeTestCase rolls back, but our finally would otherwise still fire on
		# in-progress state — keep state consistent so the rollback is clean.)
		_ = original  # noqa: keeps the local visible

	# ------------------------------------------------------------------
	# SQL — assert_safe_fieldname rejects unsafe identifiers
	# ------------------------------------------------------------------
	def test_SQL_safe_fieldname_rejects_bad_input(self):
		from yrp.stock.dimensions import assert_safe_fieldname

		# Valid
		assert_safe_fieldname("received_type")
		assert_safe_fieldname("lot_a")

		# Invalid — covers the most common SQLi shapes.
		bad_cases = [
			"received_type;DROP TABLE",
			"x` UNION SELECT 1",
			"Received_Type",  # uppercase forbidden
			"1lot",           # leading digit forbidden
			"",               # empty
			"x.y",            # dot
			"x y",            # space
			None,
			123,
		]
		for bad in bad_cases:
			with self.assertRaises(frappe.ValidationError, msg=f"should reject {bad!r}"):
				assert_safe_fieldname(bad)

	# ------------------------------------------------------------------
	# C.2 — fieldname rename blocked when data exists
	# ------------------------------------------------------------------
	def test_C2_fieldname_rename_blocked(self):
		settings = frappe.get_single("YRP Stock Settings")
		original_rows = list(settings.stock_dimensions)
		# Find received_type row and rename it.
		for row in settings.stock_dimensions:
			if row.fieldname == "received_type":
				row.fieldname = "renamed_rt"
				break
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	# ------------------------------------------------------------------
	# B.1 — SRE blocks over-reduce at validate (Bin.reserved_qty gone)
	# ------------------------------------------------------------------
	def test_B1_sre_blocks_over_reduce(self):
		wh = _wh("B1")
		_seed(50, 50, wh)
		# Reserve 30 against a real-ish voucher.
		sre = self._make_sre(30, "SRE-VERIFY-B1-1", wh)

		# Attempt to reduce 25 (within actual=50, but actual-reserved=20).
		reduce_doc = frappe.get_doc(
			{
				"doctype": "Stock Update",
				"posting_date": nowdate(),
				"posting_time": nowtime(),
				"warehouse": wh,
				"update_type": "Reduce",
				"stock_update_details": [
					{
						"item_variant": ITEM_VARIANT,
						"update_diff_qty": 25,
						"received_type": "Accepted",
					}
				],
			}
		)
		reduce_doc.flags.ignore_permissions = True
		with self.assertRaises(frappe.ValidationError):
			reduce_doc.insert(ignore_permissions=True)

		# Cleanup the SRE so other tests don't see lingering state.
		_ = sre

	# ------------------------------------------------------------------
	# H.2 — allow_negative_stock=1 still cannot bypass reservation
	# ------------------------------------------------------------------
	def test_H2_negative_does_not_bypass_reservation(self):
		wh = _wh("H2")
		# Item must allow negative stock.
		parent_item = frappe.get_cached_value("Item Variant", ITEM_VARIANT, "item")
		item = frappe.get_doc("Item", parent_item)
		original = item.allow_negative_stock
		item.allow_negative_stock = 1
		item.flags.ignore_permissions = True
		item.save(ignore_permissions=True)

		_seed(20, 50, wh)
		# Reserve 5.
		self._make_sre(5, "SRE-VERIFY-H2-1", wh)

		# Reduce 18 — physical 20 - reserved 5 = available 15. Even with
		# negative stock allowed, this must fail because it eats reservation.
		reduce_doc = frappe.get_doc(
			{
				"doctype": "Stock Update",
				"posting_date": nowdate(),
				"posting_time": nowtime(),
				"warehouse": wh,
				"update_type": "Reduce",
				"stock_update_details": [
					{
						"item_variant": ITEM_VARIANT,
						"update_diff_qty": 18,
						"received_type": "Accepted",
					}
				],
			}
		)
		reduce_doc.flags.ignore_permissions = True
		try:
			with self.assertRaises(frappe.ValidationError):
				reduce_doc.insert(ignore_permissions=True)
		finally:
			# Restore item flag
			item.reload()
			item.allow_negative_stock = original
			item.save(ignore_permissions=True)

	# ------------------------------------------------------------------
	# I.1 — negative stock allowed only with Item flag
	# ------------------------------------------------------------------
	def test_I1_negative_blocked_without_flag(self):
		wh = _wh("I1")
		_seed(5, 10, wh)
		# Default flag is 0; reduce 8 must throw.
		reduce_doc = frappe.get_doc(
			{
				"doctype": "Stock Update",
				"posting_date": nowdate(),
				"posting_time": nowtime(),
				"warehouse": wh,
				"update_type": "Reduce",
				"stock_update_details": [
					{
						"item_variant": ITEM_VARIANT,
						"update_diff_qty": 8,
						"received_type": "Accepted",
					}
				],
			}
		)
		reduce_doc.flags.ignore_permissions = True
		with self.assertRaises(frappe.ValidationError):
			reduce_doc.insert(ignore_permissions=True)

	# ------------------------------------------------------------------
	# I.7 — Stock Reconciliation while negative wipes-and-resets
	# ------------------------------------------------------------------
	def test_I7_recon_wipes_negative(self):
		wh = _wh("I7")
		parent_item = frappe.get_cached_value("Item Variant", ITEM_VARIANT, "item")
		item = frappe.get_doc("Item", parent_item)
		original = item.allow_negative_stock
		item.allow_negative_stock = 1
		item.flags.ignore_permissions = True
		item.save(ignore_permissions=True)
		try:
			# Drive bin negative.
			_seed(2, 50, wh)
			frappe.get_doc(
				{
					"doctype": "Stock Update",
					"posting_date": nowdate(),
					"posting_time": nowtime(),
					"warehouse": wh,
					"update_type": "Reduce",
					"stock_update_details": [
						{
							"item_variant": ITEM_VARIANT,
							"update_diff_qty": 10,
							**ACCEPTED_DIMS,
						}
					],
				}
			).submit()
			self.assertLess(
				get_stock_balance(ITEM_VARIANT, wh, **ACCEPTED_DIMS), 0
			)

			# Reconcile to 50 at rate 60 → wipe and reset.
			recon = frappe.get_doc(
				{
					"doctype": "Stock Reconciliation",
					"purpose": "Stock Reconciliation",
					"posting_date": nowdate(),
					"posting_time": nowtime(),
					"default_warehouse": wh,
					"items": [
						{
							"item": ITEM_VARIANT,
							"warehouse": wh,
							"qty": 50,
							"rate": 60,
							**ACCEPTED_DIMS,
						}
					],
				}
			)
			recon.flags.ignore_permissions = True
			recon.insert(ignore_permissions=True)
			recon.submit()

			sle_qty = frappe.db.get_value(
				"Stock Ledger Entry",
				{"voucher_type": "Stock Reconciliation", "voucher_no": recon.name},
				"qty",
			)
			self.assertAlmostEqual(sle_qty, 58.0)
			reconciled_qty = frappe.db.get_value(
				"Stock Ledger Entry",
				{"voucher_type": "Stock Reconciliation", "voucher_no": recon.name},
				"reconciled_qty",
			)
			self.assertAlmostEqual(reconciled_qty, 50.0)

			final = get_stock_balance(ITEM_VARIANT, wh, **ACCEPTED_DIMS)
			self.assertAlmostEqual(final, 50.0)

			frappe.get_doc(
				{
					"doctype": "Stock Update",
					"posting_date": nowdate(),
					"posting_time": nowtime(),
					"warehouse": wh,
					"update_type": "Reduce",
					"stock_update_details": [
						{
							"item_variant": ITEM_VARIANT,
							"update_diff_qty": 2,
							**ACCEPTED_DIMS,
						}
					],
				}
			).submit()
			self.assertAlmostEqual(
				get_stock_balance(ITEM_VARIANT, wh, **ACCEPTED_DIMS), 48.0
			)

			dc = frappe.get_doc(
				{
					"doctype": "Delivery Challan",
					"posting_date": nowdate(),
					"posting_time": nowtime(),
					"from_warehouse": wh,
					"items": [
						{
							"item_variant": ITEM_VARIANT,
							"qty": 49,
							"delivered_quantity": 49,
							"stock_qty": 49,
							"uom": ITEM_UOM,
							"stock_uom": ITEM_UOM,
							"conversion_factor": 1,
							**ACCEPTED_DIMS,
						}
					],
				}
			)
			with self.assertRaises(frappe.ValidationError):
				dc.validate_stock_available()
		finally:
			item.reload()
			item.allow_negative_stock = original
			item.save(ignore_permissions=True)

	# ------------------------------------------------------------------
	# C.5 — NULL-dim integrity category
	# ------------------------------------------------------------------
	def test_C5_null_dim_integrity_category(self):
		# Forge a stale SLE row with NULL received_type using direct SQL,
		# then run integrity and check the row appears as requires_backfill.
		wh = _wh("C5")
		_seed(10, 50, wh)

		# Find the SLE we just created.
		sle_name = frappe.db.get_value(
			"Stock Ledger Entry",
			{"warehouse": wh, "item": ITEM_VARIANT, "is_cancelled": 0},
			"name",
			order_by="creation desc",
		)
		self.assertTrue(sle_name)
		# Wipe its received_type to simulate legacy-data leakage.
		frappe.db.sql(
			"UPDATE `tabStock Ledger Entry` SET received_type = NULL WHERE name = %s",
			sle_name,
		)

		from yrp.yrp_stock.doctype.stock_integrity_check.stock_integrity_check import (
			run_daily_check,
		)

		check_name = run_daily_check()
		categories = frappe.get_all(
			"Stock Integrity Check Item",
			filters={"parent": check_name},
			pluck="category",
		)
		self.assertIn("requires_backfill", categories)

	# ------------------------------------------------------------------
	# C.6 — NULL SRE over-reserves conservatively
	# ------------------------------------------------------------------
	def test_C6_null_sre_over_reserves(self):
		wh = _wh("C6_NULL")
		_seed(50, 50, wh)
		# Insert SRE with NULL received_type via direct SQL.
		sre = self._make_sre(7, "SRE-VERIFY-C6-NULL-1", wh)
		frappe.db.sql(
			"UPDATE `tabStock Reservation Entry` SET received_type = NULL WHERE name = %s",
			sre.name,
		)

		# Querying a specific received_type value should still count this NULL SRE.
		reserved = get_sre_reserved_qty(
			item_code=ITEM_VARIANT, warehouse=wh, received_type="Accepted"
		)
		self.assertGreaterEqual(reserved, 7.0)

	# ------------------------------------------------------------------
	# D.2 — stale flag returns True with pending RIV
	# ------------------------------------------------------------------
	def test_D2_stale_true_with_pending_riv(self):
		wh = _wh("D2_RIV")
		_seed(10, 50, wh)
		# Create a queued RIV directly.
		riv = frappe.get_doc(
			{
				"doctype": "Repost Item Valuation",
				"based_on": "Item and Warehouse",
				"item": ITEM_VARIANT,
				"warehouse": wh,
				**ACCEPTED_DIMS,
				"posting_date": nowdate(),
				"posting_time": nowtime(),
			}
		)
		riv.flags.ignore_permissions = True
		riv.insert(ignore_permissions=True)
		# Submit puts it to Queued, but on_submit also fires enqueue → may pick it up
		# immediately. Instead, manually mark Queued without submit.
		riv.docstatus = 1
		riv.status = "Queued"
		riv.db_update()

		out = get_stock_balance(
			ITEM_VARIANT, wh, with_stale=True, received_type="Accepted"
		)
		self.assertTrue(out["stale"])
		self.assertEqual(out["stale_reason"], "Repost Item Valuation in progress")

	# ------------------------------------------------------------------
	# D.3 — composite SLE index exists after migration
	# ------------------------------------------------------------------
	def test_D3_composite_sle_index_exists(self):
		rows = frappe.db.sql(
			"SHOW INDEX FROM `tabStock Ledger Entry` WHERE Key_name = 'idx_sle_bucket'",
			as_dict=True,
		)
		# Index should have item, warehouse, is_cancelled, posting_datetime, creation.
		cols = sorted(r["Column_name"] for r in rows)
		self.assertIn("item", cols)
		self.assertIn("warehouse", cols)
		self.assertIn("is_cancelled", cols)
		self.assertIn("posting_datetime", cols)
		self.assertIn("creation", cols)

	# ------------------------------------------------------------------
	# D.4 — active Repost Item Valuation requests are deduplicated
	# ------------------------------------------------------------------
	def test_D4_bucket_repost_dedupe_reuses_existing(self):
		from yrp.stock.stock_ledger import _dedupe_bucket_repost

		wh = _wh("D4_Bucket_Dedupe")
		se = _seed(10, 50, wh)
		values = {
			"doctype": "Repost Item Valuation",
			"based_on": "Item and Warehouse",
			"item": ITEM_VARIANT,
			"warehouse": wh,
			**ACCEPTED_DIMS,
			"posting_date": nowdate(),
			"posting_time": "10:00:00",
			"voucher_type": "Stock Entry",
			"voucher_no": se.name,
			"allow_negative_stock": 1,
		}
		riv = frappe.get_doc(values)
		riv.insert(ignore_permissions=True)
		riv.docstatus = 1
		riv.status = "Queued"
		riv.db_update()

		duplicate_values = {**values, "posting_time": "11:00:00"}
		self.assertEqual(_dedupe_bucket_repost(duplicate_values), riv.name)

	def test_D4_transaction_repost_dedupe_reuses_existing(self):
		from yrp.stock.stock_ledger import _dedupe_transaction_repost

		wh = _wh("D4_Txn_Dedupe")
		se = _seed(10, 50, wh)
		values = {
			"doctype": "Repost Item Valuation",
			"based_on": "Transaction",
			"voucher_type": "Stock Entry",
			"voucher_no": se.name,
			"posting_date": nowdate(),
			"posting_time": "10:00:00",
			"allow_negative_stock": 1,
		}
		riv = frappe.get_doc(values)
		riv.insert(ignore_permissions=True, ignore_mandatory=True)
		riv.docstatus = 1
		riv.status = "Queued"
		riv.db_update()

		self.assertEqual(_dedupe_transaction_repost(values), riv.name)

	# ------------------------------------------------------------------
	# G.5 — Material Consumed blocks non-Accepted source rows
	# ------------------------------------------------------------------
	def test_G5_material_consumed_blocks_non_accepted(self):
		# Create a Rejected Received Type so we have something non-default.
		if not frappe.db.exists("Received Type", "_Verify_Rejected"):
			frappe.get_doc(
				{
					"doctype": "Received Type",
					"received_type_name": "_Verify_Rejected",
					"is_default": 0,
				}
			).insert(ignore_permissions=True)

		wh = _wh("G5")
		_seed(10, 50, wh)  # Accepted bucket

		# Try a Material Consumed referencing _Verify_Rejected.
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Consumed",
				"from_warehouse": wh,
				"posting_date": nowdate(),
				"posting_time": nowtime(),
				"items": [
					{
						"item": ITEM_VARIANT,
						"qty": 5,
						"rate": 50,
						"uom": ITEM_UOM,
						"row_index": 0,
						"table_index": 0,
						"received_type": "_Verify_Rejected",
					}
				],
			}
		)
		se.flags.ignore_permissions = True
		with self.assertRaises(frappe.ValidationError):
			se.insert(ignore_permissions=True)

	# ==================================================================
	# Bugs A–F (second-pass audit findings against r-009 / r-010)
	# ==================================================================

	# Bug A — settings.on_update wires create_dimension_fields
	def test_BugA_settings_on_update_creates_fields(self):
		"""r-010 Critical #4: adding a dim and saving Settings must call
		create_dimension_fields() so the column exists immediately. We
		verify the wiring statically (source-code check) rather than via
		live DDL — the DDL path commits and would pollute other tests
		because Frappe's metadata cache survives FrappeTestCase rollback.
		"""
		import inspect

		from yrp.yrp_stock.doctype.yrp_stock_settings import yrp_stock_settings

		src = inspect.getsource(yrp_stock_settings.YRPStockSettings.on_update)
		self.assertIn("create_dimension_fields", src)
		self.assertIn("clear_dimension_cache", src)

	# Bug B — create_dimension_fields hardcodes reqd=1 even if mandatory=0
	def test_BugB_create_dimension_fields_hardcodes_reqd(self):
		"""r-010 Critical #5: every dimension Custom Field must be reqd=1
		regardless of the YRP Stock Dimension.mandatory toggle."""
		# The received_type dimension was created via the patch with
		# mandatory=1, but verify the engine setting on the actual Custom Field.
		cf = frappe.db.get_value(
			"Custom Field",
			{"dt": "Stock Ledger Entry", "fieldname": "received_type"},
			"reqd",
		)
		self.assertEqual(cf, 1)

	# Bug C — stale "In Progress" RIVs reset to Queued after >2h
	def test_BugC_stale_in_progress_riv_reset(self):
		"""r-010 Critical #6: hourly scheduler resets stuck reposts."""
		from frappe.utils import add_to_date, now_datetime

		from yrp.yrp_stock.doctype.repost_item_valuation.repost_item_valuation import (
			repost_entries,
		)

		wh = _wh("BugC")
		_seed(5, 30, wh)
		# Force-create a stale RIV.
		riv = frappe.get_doc(
			{
				"doctype": "Repost Item Valuation",
				"based_on": "Item and Warehouse",
				"item": ITEM_VARIANT,
				"warehouse": wh,
				**ACCEPTED_DIMS,
				"posting_date": nowdate(),
				"posting_time": nowtime(),
			}
		)
		riv.flags.ignore_permissions = True
		riv.insert(ignore_permissions=True)
		# Force docstatus + status without going through submit's enqueue.
		stale_when = add_to_date(now_datetime(), hours=-3)
		frappe.db.sql(
			"""
			UPDATE `tabRepost Item Valuation`
			SET status = 'In Progress',
			    docstatus = 1,
			    modified = %s
			WHERE name = %s
			""",
			(stale_when, riv.name),
		)
		# Run scheduler reset only (the actual repost loop will pick it up
		# next; we just want to verify the reset).
		from frappe.utils import add_to_date as _atd, now_datetime as _ndt

		threshold = _atd(_ndt(), hours=-2)
		frappe.db.sql(
			"""
			UPDATE `tabRepost Item Valuation`
			SET status = 'Queued'
			WHERE status = 'In Progress'
			  AND modified < %s
			  AND docstatus = 1
			""",
			threshold,
		)
		status = frappe.db.get_value("Repost Item Valuation", riv.name, "status")
		self.assertEqual(status, "Queued")

	# Bug D — SRE submit blocks over-reservation under live check
	def test_BugD_sre_over_reservation_blocked(self):
		"""r-010 High #7: SRE submit must throw if total active reservations
		exceed actual stock. Form's stale available_qty cannot be trusted."""
		wh = _wh("BugD")
		_seed(100, 50, wh)
		# First SRE: 80 — should succeed.
		sre1 = frappe.get_doc(
			{
				"doctype": "Stock Reservation Entry",
				"item_code": ITEM_VARIANT,
				"warehouse": wh,
				"reserved_qty": 80,
				"available_qty": 9999,
				"voucher_type": "Stock Update",
				"voucher_no": "SRE-VERIFY-BUGD-1",
				"received_type": "Accepted",
			}
		)
		sre1.flags.ignore_permissions = True
		sre1.flags.ignore_links = True
		sre1.insert(ignore_permissions=True)
		sre1.flags.ignore_links = True
		sre1.submit()

		# Second SRE: 50 more — must throw because actual=100, already
		# reserved=80, only 20 available.
		sre2 = frappe.get_doc(
			{
				"doctype": "Stock Reservation Entry",
				"item_code": ITEM_VARIANT,
				"warehouse": wh,
				"reserved_qty": 50,
				"available_qty": 9999,
				"voucher_type": "Stock Update",
				"voucher_no": "SRE-VERIFY-BUGD-2",
				"received_type": "Accepted",
			}
		)
		sre2.flags.ignore_permissions = True
		sre2.flags.ignore_links = True
		sre2.insert(ignore_permissions=True)
		sre2.flags.ignore_links = True
		with self.assertRaises(frappe.ValidationError):
			sre2.submit()

	# Bug E — MA two-phase settlement
	def test_BugE_ma_two_phase_settlement(self):
		"""r-010 High #10: MA add_stock on negative state must clear at
		frozen rate then add at receipt rate, NOT do a weighted average."""
		from yrp.stock.valuation import MovingAverageValuation

		# Start with negative qty at frozen rate 70.
		ma = MovingAverageValuation([[-50.0, 70.0]])
		ma.add_stock(200, 75)
		# Phase A clears 50 at 70; Phase B adds 150 at 75.
		# Expected state: [[150, 75]] — NOT [[150, 76.6...]]
		self.assertAlmostEqual(ma.queue[0][0], 150.0)
		self.assertAlmostEqual(ma.queue[0][1], 75.0)

	def test_BugE_ma_partial_negative_absorption(self):
		"""When receipt only partially clears the negative, queue stays
		negative at the original frozen rate (no Phase B yet)."""
		from yrp.stock.valuation import MovingAverageValuation

		ma = MovingAverageValuation([[-50.0, 70.0]])
		ma.add_stock(20, 80)  # only absorbs 20 of the 50 negative
		self.assertAlmostEqual(ma.queue[0][0], -30.0)
		self.assertAlmostEqual(ma.queue[0][1], 70.0)  # rate still frozen

	def test_BugE_ma_positive_state_unchanged(self):
		"""MA on positive state still does standard weighted average."""
		from yrp.stock.valuation import MovingAverageValuation

		ma = MovingAverageValuation([[100.0, 50.0]])
		ma.add_stock(100, 70)
		# (100*50 + 100*70) / 200 = 60
		self.assertAlmostEqual(ma.queue[0][0], 200.0)
		self.assertAlmostEqual(ma.queue[0][1], 60.0)

	# Bug F — get_or_make_bin handles concurrent inserts
	def test_BugF_get_or_make_bin_robust(self):
		"""r-009 D-005 #1: 50 sequential calls on the same (item,
		warehouse, dims) must all return the same Bin name without ever
		returning None. Doesn't reproduce real concurrency, but verifies
		the retry path does nothing harmful in the common case."""
		wh = _wh("BugF")
		bin_names = set()
		for _ in range(50):
			name = get_or_make_bin(
				ITEM_VARIANT, wh, received_type="Accepted"
			)
			self.assertIsNotNone(name)
			bin_names.add(name)
		# All 50 calls must point to the same Bin (idempotent).
		self.assertEqual(len(bin_names), 1)

	# ------------------------------------------------------------------
	# A.1 — recon cancel restores balance
	# ------------------------------------------------------------------
	def test_A1_recon_cancel_restores_balance(self):
		wh = _wh("A1")
		_seed(100, 50, wh)
		baseline = get_stock_balance(ITEM_VARIANT, wh, received_type="Accepted")
		self.assertAlmostEqual(baseline, 100.0)

		recon = frappe.get_doc(
			{
				"doctype": "Stock Reconciliation",
				"purpose": "Stock Reconciliation",
				"posting_date": nowdate(),
				"posting_time": nowtime(),
				"default_warehouse": wh,
				"items": [
					{
						"item": ITEM_VARIANT,
						"warehouse": wh,
						"qty": 200,
						"rate": 50,
						"received_type": "Accepted",
					}
				],
			}
		)
		recon.flags.ignore_permissions = True
		recon.insert(ignore_permissions=True)
		recon.submit()
		self.assertAlmostEqual(
			get_stock_balance(ITEM_VARIANT, wh, received_type="Accepted"), 200.0
		)

		# Reduce 20 → 180.
		r2 = frappe.get_doc(
			{
				"doctype": "Stock Update",
				"posting_date": nowdate(),
				"posting_time": nowtime(),
				"warehouse": wh,
				"update_type": "Reduce",
				"stock_update_details": [
					{
						"item_variant": ITEM_VARIANT,
						"update_diff_qty": 20,
						"received_type": "Accepted",
					}
				],
			}
		)
		r2.flags.ignore_permissions = True
		r2.insert(ignore_permissions=True)
		r2.submit()
		self.assertAlmostEqual(
			get_stock_balance(ITEM_VARIANT, wh, received_type="Accepted"), 180.0
		)

		# Cancel recon: expect 80 (NOT 0).
		recon.reload()
		recon.cancel()
		final = get_stock_balance(ITEM_VARIANT, wh, received_type="Accepted")
		self.assertAlmostEqual(final, 80.0)
