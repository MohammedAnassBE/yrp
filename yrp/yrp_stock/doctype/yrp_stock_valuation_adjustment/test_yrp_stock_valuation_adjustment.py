from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, getdate, nowdate

from yrp.stock.dimensions import get_mandatory_dimensions
from yrp.stock.utils import get_stock_balance
from yrp.yrp_stock.doctype.yrp_stock_valuation_adjustment.yrp_stock_valuation_adjustment import (
	create_adjustment,
	create_reversal,
	enqueue_adjustment,
	is_stock_adjustment_enabled,
	process_adjustment,
	register_production_links,
)


def _stock_context():
	item_variant = frappe.db.sql(
		"""
		SELECT iv.name
		FROM `tabYRP Item Variant` iv
		INNER JOIN `tabYRP Item` i ON i.name = iv.item
		WHERE i.is_stock_item = 1
		ORDER BY iv.creation, iv.name
		LIMIT 1
		""",
	)
	item_variant = item_variant[0][0] if item_variant else None
	if not item_variant:
		raise frappe.DoesNotExistError("Valuation tests require one Item Variant")
	item = frappe.db.get_value('YRP Item Variant', item_variant, "item")
	uom = frappe.db.get_value('YRP Item', item, "default_unit_of_measure")
	if not uom:
		raise frappe.DoesNotExistError(f"{item} requires a default UOM")
	dimensions = {}
	for dimension in get_mandatory_dimensions():
		fieldname = dimension["fieldname"]
		if fieldname == "received_type":
			value = frappe.db.get_single_value(
				'YRP YRP Stock Settings', "default_received_type"
			)
		else:
			value = frappe.db.get_value(
				dimension.get("dimension_doctype"), {}, "name"
			)
		if not value:
			raise frappe.DoesNotExistError(
				f"Valuation tests require {dimension.get('dimension_doctype') or fieldname}"
			)
		dimensions[fieldname] = value
	return item_variant, uom, dimensions


def _warehouse(label):
	return frappe.get_doc(
		{
			"doctype": 'YRP Warehouse',
			"name1": f"_Test Valuation {label} {frappe.generate_hash(length=8)}",
		}
	).insert(ignore_permissions=True).name


def _stock_entry(
	item,
	uom,
	dimensions,
	purpose,
	qty,
	rate,
	*,
	from_warehouse=None,
	to_warehouse=None,
	posting_time="09:00:00",
):
	doc = frappe.get_doc(
		{
			"doctype": 'YRP Stock Entry',
			"purpose": purpose,
			"from_warehouse": from_warehouse,
			"to_warehouse": to_warehouse,
			"skip_transit": 1,
			"edit_posting_date_and_time": 1,
			"posting_date": nowdate(),
			"posting_time": posting_time,
			"items": [
				{
					"item": item,
					"qty": qty,
					"rate": rate,
					"uom": uom,
					"row_index": 0,
					"table_index": 0,
					**dimensions,
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


class TestStockValuationAdjustment(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item, cls.uom, cls.dimensions = _stock_context()

	def setUp(self):
		super().setUp()
		setting = patch(
			"yrp.yrp_stock.doctype.yrp_stock_valuation_adjustment.yrp_stock_valuation_adjustment.is_stock_adjustment_enabled",
			return_value=True,
		)
		setting.start()
		self.addCleanup(setting.stop)

	def _adjust(self, receipt, difference, *, apply=True):
		sle = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			{
				"voucher_type": receipt.doctype,
				"voucher_no": receipt.name,
				"qty": [">", 0],
				"is_cancelled": 0,
			},
			["name", "item", "qty", "rate"],
			as_dict=True,
		)
		name = create_adjustment(
			adjustment_type="Work Order Excess Usage",
			source_doctype=receipt.doctype,
			source_name=receipt.name,
			effective_date=receipt.posting_date,
			allocations=[
				{
					"target_sle": sle.name,
					"item": sle.item,
					"quantity": sle.qty,
					"old_rate": sle.rate,
					"new_rate": sle.rate + difference / sle.qty,
					"difference": difference,
					"allocation_weight": sle.qty,
					"stock_dimensions": frappe.as_json(self.dimensions),
				}
			],
			idempotency_key=f"test:{receipt.name}:{difference}",
			enqueue=False,
		)
		if apply:
			process_adjustment(name)
		return name, sle.name

	def test_settings_gate_skips_new_adjustment_creation(self):
		warehouse = _warehouse("Disabled Adjustment")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
		)
		with patch(
			"yrp.yrp_stock.doctype.yrp_stock_valuation_adjustment.yrp_stock_valuation_adjustment.is_stock_adjustment_enabled",
			return_value=False,
		):
			adjustment, sle_name = self._adjust(receipt, 10, apply=False)

		self.assertIsNone(adjustment)
		self.assertFalse(
			frappe.db.exists(
				'YRP Stock Valuation Adjustment Source', {"target_sle": sle_name}
			)
		)
		self.assertEqual(
			frappe.db.get_value(
				'YRP Stock Ledger Entry', sle_name, "valuation_is_stale"
			),
			0,
		)

	def test_settings_gate_reads_the_checkbox_value(self):
		with patch.object(frappe.db, "get_single_value", return_value=0):
			self.assertFalse(is_stock_adjustment_enabled())
		with patch.object(frappe.db, "get_single_value", return_value=1):
			self.assertTrue(is_stock_adjustment_enabled())

	def test_adjustment_job_timeout_is_twenty_five_minutes(self):
		warehouse = _warehouse("Adjustment Timeout")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
		)
		adjustment, _sle_name = self._adjust(receipt, 10, apply=False)
		with patch.object(frappe, "enqueue") as enqueue:
			enqueue_adjustment(adjustment)

		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs["timeout"], 25 * 60)

	def test_adjustment_revalues_remaining_stock_without_changing_original_rate(self):
		warehouse = _warehouse("Remaining")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
		)

		adjustment, sle_name = self._adjust(receipt, 10)
		sle = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			sle_name,
			["qty", "rate", "valuation_adjustment_value", "stock_value"],
			as_dict=True,
		)
		parent = frappe.db.get_value(
			'YRP Stock Valuation Adjustment',
			adjustment,
			["status", "propagated_stock_difference", "terminal_difference"],
			as_dict=True,
		)
		self.assertAlmostEqual(flt(sle.rate), 10)
		self.assertAlmostEqual(flt(sle.valuation_adjustment_value), 10)
		self.assertEqual(frappe.db.get_value('YRP Stock Ledger Entry', sle_name, "valuation_is_stale"), 0)
		self.assertAlmostEqual(
			flt(sle.stock_value), flt(sle.qty) * flt(sle.rate) + 10
		)
		self.assertEqual(parent.status, "Completed")
		self.assertAlmostEqual(flt(parent.propagated_stock_difference), 10)
		self.assertAlmostEqual(flt(parent.terminal_difference), 0)
		with self.assertRaisesRegex(frappe.ValidationError, "immutable audit"):
			frappe.get_doc(
				'YRP Stock Valuation Adjustment', adjustment
			).before_cancel()

		# A duplicated/retried RQ delivery must be a no-op after completion.
		process_adjustment(adjustment)
		self.assertAlmostEqual(
			flt(
				frappe.db.get_value(
					'YRP Stock Ledger Entry', sle_name, "valuation_adjustment_value"
				)
			),
			10,
		)

	def test_queued_adjustment_is_visible_as_stale_in_stock_balance(self):
		warehouse = _warehouse("Queued Stale")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
		)
		adjustment, _sle_name = self._adjust(receipt, 10, apply=False)

		balance = get_stock_balance(
			self.item,
			warehouse,
			with_stale=True,
			**self.dimensions,
		)
		self.assertTrue(balance["stale"])
		self.assertEqual(
			balance["stale_reason"], "Stock Valuation Adjustment in progress"
		)

		process_adjustment(adjustment)
		balance = get_stock_balance(
			self.item,
			warehouse,
			with_stale=True,
			**self.dimensions,
		)
		self.assertFalse(balance["stale"])
		self.assertIsNone(balance["stale_reason"])

	def test_retry_releases_parent_lock_before_applying_existing_entries(self):
		warehouse = _warehouse("Retry Lock Order")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
		)
		adjustment, _sle_name = self._adjust(receipt, 10, apply=False)
		module = (
			"yrp.yrp_stock.doctype.yrp_stock_valuation_adjustment."
			"stock_valuation_adjustment"
		)

		# First delivery persists the propagation plan but simulates a worker
		# stopping before it applies the first target.
		with patch(f"{module}._apply_chunk"):
			process_adjustment(adjustment)

		events = []
		real_commit = frappe.db.commit

		def commit_before_apply():
			events.append("commit")
			return real_commit()

		with (
			patch.object(frappe.db, "commit", side_effect=commit_before_apply),
			patch(f"{module}._apply_chunk", side_effect=lambda _name: events.append("apply")),
		):
			process_adjustment(adjustment)

		self.assertEqual(events[:2], ["commit", "apply"])
		process_adjustment(adjustment)

	def test_reversal_waits_until_the_original_adjustment_is_applied(self):
		warehouse = _warehouse("Early Reversal")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
		)
		original, sle_name = self._adjust(receipt, 10, apply=False)
		reversal = create_reversal(receipt.doctype, receipt.name)[0]

		with patch(
			"yrp.yrp_stock.doctype.yrp_stock_valuation_adjustment.yrp_stock_valuation_adjustment.enqueue_adjustment"
		) as enqueue:
			# Simulate two workers selecting the cancellation job first.
			process_adjustment(reversal)
			self.assertAlmostEqual(
				flt(
					frappe.db.get_value(
						'YRP Stock Ledger Entry', sle_name, "valuation_adjustment_value"
					)
				),
				0,
			)
			self.assertEqual(
				frappe.db.get_value(
					'YRP Stock Valuation Adjustment', reversal, "status"
				),
				"Queued",
			)

			process_adjustment(original)
			self.assertEqual(
				frappe.db.get_value(
					'YRP Stock Valuation Adjustment', original, "status"
				),
				"Reversal Queued",
			)
			enqueue.assert_any_call(reversal)
			self.assertEqual(
				frappe.db.get_value(
					'YRP Stock Ledger Entry', sle_name, "valuation_is_stale"
				),
				1,
			)

			process_adjustment(reversal)

		self.assertAlmostEqual(
			flt(
				frappe.db.get_value(
					'YRP Stock Ledger Entry', sle_name, "valuation_adjustment_value"
				)
			),
			0,
		)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Valuation Adjustment', original, "status"),
			"Reversed",
		)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Ledger Entry', sle_name, "valuation_is_stale"),
			0,
		)

	def test_duplicate_original_job_cannot_regress_reversal_queued_state(self):
		warehouse = _warehouse("Duplicate Original")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
		)
		original, _sle_name = self._adjust(receipt, 10)
		reversal = create_reversal(receipt.doctype, receipt.name)[0]
		self.assertEqual(
			frappe.db.get_value('YRP Stock Valuation Adjustment', original, "status"),
			"Reversal Queued",
		)
		with patch(
			"yrp.yrp_stock.doctype.yrp_stock_valuation_adjustment.yrp_stock_valuation_adjustment.enqueue_adjustment"
		) as enqueue:
			process_adjustment(original)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Valuation Adjustment', original, "status"),
			"Reversal Queued",
		)
		enqueue.assert_called_once_with(reversal, retry=True)

	def test_voucher_cannot_cancel_while_an_adjustment_owns_its_receipt(self):
		warehouse = _warehouse("Active Voucher Guard")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
		)
		adjustment, _sle_name = self._adjust(receipt, 10, apply=False)
		self.assertEqual(
			frappe.db.get_value(
				'YRP Stock Valuation Adjustment', adjustment, "status"
			),
			"Queued",
		)
		frappe.db.savepoint("before_active_valuation_cancel")
		with self.assertRaisesRegex(
			frappe.ValidationError, "unfinished Stock Valuation Adjustment"
		):
			receipt.cancel()
		# The request boundary rolls the failed cancellation back. Reproduce that
		# boundary explicitly because this test intentionally catches the exception.
		frappe.db.rollback(save_point="before_active_valuation_cancel")
		self.assertEqual(
			frappe.db.get_value(receipt.doctype, receipt.name, "docstatus"),
			1,
		)

	def test_closed_period_blocks_adjustment_and_reversal_before_enqueue(self):
		warehouse = _warehouse("Closed Period")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
		)
		with patch(
			"yrp.stock.stock_ledger.get_last_stock_valuation_closing_date",
			return_value=getdate(receipt.posting_date),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "closed through"):
				self._adjust(receipt, 10, apply=False)
		self.assertFalse(
			frappe.db.exists(
				'YRP Stock Valuation Adjustment',
				{"idempotency_key": f"test:{receipt.name}:10"},
			)
		)

		original, _sle_name = self._adjust(receipt, 10)
		with patch(
			"yrp.stock.stock_ledger.get_last_stock_valuation_closing_date",
			return_value=getdate(receipt.posting_date),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "closed through"):
				create_reversal(receipt.doctype, receipt.name)
		self.assertFalse(
			frappe.db.exists(
				'YRP Stock Valuation Adjustment', {"reversal_of": original, "docstatus": 1}
			)
		)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Valuation Adjustment', original, "status"),
			"Completed",
		)

	def test_effective_date_is_the_earliest_affected_receipt_date(self):
		warehouse = _warehouse("Effective Date")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			2,
			10,
			to_warehouse=warehouse,
		)
		sle = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			{"voucher_no": receipt.name, "qty": [">", 0], "is_cancelled": 0},
			["name", "item", "posting_date"],
			as_dict=True,
		)
		adjustment = create_adjustment(
			adjustment_type="Work Order Excess Usage",
			source_doctype=receipt.doctype,
			source_name=receipt.name,
			effective_date=add_days(sle.posting_date, 5),
			allocations=[
				{
					"target_sle": sle.name,
					"item": sle.item,
					"quantity": 2,
					"difference": 2,
					"allocation_weight": 2,
				}
			],
			idempotency_key=f"test:effective-date:{receipt.name}",
			enqueue=False,
		)
		self.assertEqual(
			getdate(
				frappe.db.get_value(
					'YRP Stock Valuation Adjustment', adjustment, "effective_date"
				)
			),
			getdate(sle.posting_date),
		)

	def test_rate_decrease_propagates_as_a_signed_negative_difference(self):
		warehouse = _warehouse("Rate Decrease")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
		)

		adjustment, sle_name = self._adjust(receipt, -10)
		sle = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			sle_name,
			["qty", "rate", "valuation_adjustment_value", "stock_value"],
			as_dict=True,
		)
		parent = frappe.db.get_value(
			'YRP Stock Valuation Adjustment',
			adjustment,
			["status", "propagated_stock_difference", "terminal_difference"],
			as_dict=True,
		)
		self.assertAlmostEqual(flt(sle.rate), 10)
		self.assertAlmostEqual(flt(sle.valuation_adjustment_value), -10)
		self.assertAlmostEqual(
			flt(sle.stock_value), flt(sle.qty) * flt(sle.rate) - 10
		)
		self.assertEqual(parent.status, "Completed")
		self.assertAlmostEqual(flt(parent.propagated_stock_difference), -10)
		self.assertAlmostEqual(flt(parent.terminal_difference), 0)

	def test_adjustment_follows_persisted_transfer_pair(self):
		source = _warehouse("Transfer Source")
		target = _warehouse("Transfer Target")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=source,
			posting_time="09:10:00",
		)
		transfer = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Send to Warehouse",
			5,
			0,
			from_warehouse=source,
			to_warehouse=target,
			posting_time="09:11:00",
		)
		transfer_sles = frappe.get_all(
			'YRP Stock Ledger Entry',
			filters={"voucher_no": transfer.name, "is_cancelled": 0},
			fields=["name", "qty", "paired_stock_ledger_entry"],
			order_by="creation asc",
		)
		outgoing = next(row for row in transfer_sles if flt(row.qty) < 0)
		incoming = next(row for row in transfer_sles if flt(row.qty) > 0)
		self.assertEqual(outgoing.paired_stock_ledger_entry, incoming.name)
		self.assertEqual(incoming.paired_stock_ledger_entry, outgoing.name)

		adjustment, _sle_name = self._adjust(receipt, 10)
		incoming_overlay = frappe.db.get_value(
			'YRP Stock Ledger Entry', incoming.name, "valuation_adjustment_value"
		)
		parent = frappe.db.get_value(
			'YRP Stock Valuation Adjustment',
			adjustment,
			["status", "propagated_stock_difference", "terminal_difference"],
			as_dict=True,
		)
		self.assertAlmostEqual(flt(incoming_overlay), 5)
		self.assertEqual(parent.status, "Completed")
		self.assertAlmostEqual(flt(parent.propagated_stock_difference), 10)
		self.assertAlmostEqual(flt(parent.terminal_difference), 0)

	def test_same_rate_fifo_receipts_keep_late_cost_on_the_consumed_layer(self):
		source = _warehouse("Same Rate Source")
		target = _warehouse("Same Rate Target")
		first = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=source,
			posting_time="09:05:00",
		)
		_stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=source,
			posting_time="09:06:00",
		)
		transfer = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Send to Warehouse",
			5,
			0,
			from_warehouse=source,
			to_warehouse=target,
			posting_time="09:07:00",
		)
		incoming = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			{"voucher_no": transfer.name, "qty": [">", 0], "is_cancelled": 0},
			"name",
		)
		adjustment, _sle_name = self._adjust(first, 10)
		self.assertAlmostEqual(
			flt(
				frappe.db.get_value(
					'YRP Stock Ledger Entry', incoming, "valuation_adjustment_value"
				)
			),
			5,
		)
		reversal = create_reversal(first.doctype, first.name)[0]
		process_adjustment(reversal)
		self.assertAlmostEqual(
			flt(
				frappe.db.get_value(
					'YRP Stock Ledger Entry', incoming, "valuation_adjustment_value"
				)
			),
			0,
		)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Valuation Adjustment', adjustment, "status"),
			"Reversed",
		)

	def test_fully_issued_stock_becomes_a_terminal_difference(self):
		warehouse = _warehouse("Terminal Issue")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
			posting_time="09:15:00",
		)
		_stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Issue",
			10,
			0,
			from_warehouse=warehouse,
			posting_time="09:16:00",
		)

		adjustment, _sle_name = self._adjust(receipt, 10)
		parent = frappe.db.get_value(
			'YRP Stock Valuation Adjustment',
			adjustment,
			["status", "propagated_stock_difference", "terminal_difference"],
			as_dict=True,
		)
		self.assertEqual(parent.status, "Completed")
		self.assertAlmostEqual(flt(parent.propagated_stock_difference), 0)
		self.assertAlmostEqual(flt(parent.terminal_difference), 10)

		reversal = create_reversal(receipt.doctype, receipt.name)[0]
		process_adjustment(reversal)
		reversal_result = frappe.db.get_value(
			'YRP Stock Valuation Adjustment',
			reversal,
			["status", "propagated_stock_difference", "terminal_difference"],
			as_dict=True,
		)
		self.assertEqual(reversal_result.status, "Completed")
		self.assertAlmostEqual(flt(reversal_result.propagated_stock_difference), 0)
		self.assertAlmostEqual(flt(reversal_result.terminal_difference), -10)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Valuation Adjustment', adjustment, "status"),
			"Reversed",
		)

	def test_adjustment_crosses_a_persisted_production_link(self):
		input_warehouse = _warehouse("Production Input")
		output_warehouse = _warehouse("Production Output")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=input_warehouse,
			posting_time="09:20:00",
		)
		consumption = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Issue",
			5,
			0,
			from_warehouse=input_warehouse,
			posting_time="09:21:00",
		)
		output = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			5,
			20,
			to_warehouse=output_warehouse,
			posting_time="09:22:00",
		)
		consumption_sle = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			{"voucher_no": consumption.name, "qty": ["<", 0], "is_cancelled": 0},
			"name",
		)
		output_sle = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			{"voucher_no": output.name, "qty": [">", 0], "is_cancelled": 0},
			"name",
		)
		link_name = register_production_links(
			consumption.doctype,
			consumption.name,
			[
				{
					"consumption_sle": consumption_sle,
					"output_receipt_sle": output_sle,
					"source_row": consumption.items[0].name,
					"input_quantity": 5,
					"allocation_weight": 5,
					"stock_dimensions": frappe.as_json(self.dimensions),
				}
			],
		)[0]

		adjustment, _sle_name = self._adjust(receipt, 10)
		output_overlay = frappe.db.get_value(
			'YRP Stock Ledger Entry', output_sle, "valuation_adjustment_value"
		)
		parent = frappe.db.get_value(
			'YRP Stock Valuation Adjustment',
			adjustment,
			["status", "propagated_stock_difference", "terminal_difference"],
			as_dict=True,
		)
		self.assertAlmostEqual(flt(output_overlay), 5)
		self.assertEqual(parent.status, "Completed")
		self.assertAlmostEqual(flt(parent.propagated_stock_difference), 10)
		self.assertAlmostEqual(flt(parent.terminal_difference), 0)

		# Completed propagated value is immutable lineage until its signed
		# reversal finishes. Cancellation must not strand the ₹5 overlay on a
		# cancelled receipt or leave its production edge traversable.
		frappe.db.savepoint("before_completed_lineage_cancel")
		with self.assertRaisesRegex(
			frappe.ValidationError, "completed Stock Valuation Adjustment"
		):
			output.cancel()
		frappe.db.rollback(save_point="before_completed_lineage_cancel")
		self.assertEqual(
			frappe.db.get_value(output.doctype, output.name, "docstatus"), 1
		)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Ledger Entry', output_sle, "is_cancelled"), 0
		)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Valuation Production Link', link_name, "active"),
			1,
		)

		reversal = create_reversal(receipt.doctype, receipt.name)[0]
		process_adjustment(reversal)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Valuation Adjustment', adjustment, "status"),
			"Reversed",
		)
		self.assertAlmostEqual(
			flt(
				frappe.db.get_value(
					'YRP Stock Ledger Entry', output_sle, "valuation_adjustment_value"
				)
			),
			0,
		)

		output.reload()
		output.cancel()
		self.assertEqual(output.docstatus, 2)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Ledger Entry', output_sle, "is_cancelled"), 1
		)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Valuation Production Link', link_name, "active"),
			0,
		)

	def test_late_consumption_can_revalue_an_earlier_production_output(self):
		input_warehouse = _warehouse("Late Production Input")
		output_warehouse = _warehouse("Earlier Production Output")
		receipt = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			10,
			10,
			to_warehouse=input_warehouse,
			posting_time="10:00:00",
		)
		output = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Receipt",
			5,
			20,
			to_warehouse=output_warehouse,
			posting_time="10:01:00",
		)
		late_consumption = _stock_entry(
			self.item,
			self.uom,
			self.dimensions,
			"Material Issue",
			5,
			0,
			from_warehouse=input_warehouse,
			posting_time="10:02:00",
		)
		consumption_sle = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			{"voucher_no": late_consumption.name, "qty": ["<", 0], "is_cancelled": 0},
			"name",
		)
		output_sle = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			{"voucher_no": output.name, "qty": [">", 0], "is_cancelled": 0},
			"name",
		)

		link_name = register_production_links(
			late_consumption.doctype,
			late_consumption.name,
			[
				{
					"consumption_sle": consumption_sle,
					"output_receipt_sle": output_sle,
					"source_row": late_consumption.items[0].name,
					"input_quantity": 5,
					"allocation_weight": 5,
					"stock_dimensions": frappe.as_json(self.dimensions),
				}
			],
		)[0]

		adjustment, _sle_name = self._adjust(receipt, 10)
		parent = frappe.db.get_value(
			'YRP Stock Valuation Adjustment',
			adjustment,
			["status", "propagated_stock_difference", "terminal_difference"],
			as_dict=True,
		)
		self.assertEqual(
			frappe.db.get_value('YRP Stock Valuation Production Link', link_name, "active"),
			1,
		)
		self.assertAlmostEqual(
			flt(
				frappe.db.get_value(
					'YRP Stock Ledger Entry', output_sle, "valuation_adjustment_value"
				)
			),
			5,
		)
		self.assertEqual(parent.status, "Completed")
		self.assertAlmostEqual(flt(parent.propagated_stock_difference), 10)
		self.assertAlmostEqual(flt(parent.terminal_difference), 0)
