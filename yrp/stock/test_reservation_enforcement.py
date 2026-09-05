"""Unit coverage for central stock-reservation enforcement.

These tests deliberately avoid a site/database dependency.  They exercise the
ledger replay boundary that every stock-producing controller shares; voucher
integration tests remain in their owning DocType test modules.
"""

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from yrp.stock.stock_ledger import (
	NegativeStockError,
	UpdateEntriesAfter,
	_valuation_bucket_has_active_reservation,
)
from yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan import YRPDeliveryChallan


def _raise_frappe_error(message, exc=frappe.ValidationError, **kwargs):
	raise exc(message)


def _engine(*, reserved_stock=0, is_cancelled=0, validate_reserved_stock=1):
	"""Build only the state needed by the reservation guard."""
	engine = UpdateEntriesAfter.__new__(UpdateEntriesAfter)
	engine.args = frappe._dict(
		{
			"item": "ITEM-1",
			"warehouse": "WH-1",
			"lot": "LOT-1",
			"received_type": "Accepted",
			"reserved_stock": reserved_stock,
			"validate_reserved_stock": validate_reserved_stock,
			"is_cancelled": is_cancelled,
		}
	)
	engine.dim_fields = ["lot", "received_type"]
	engine.valuation_dim_fields = ["lot"]
	engine.valuation_method = "FIFO"
	engine.allow_negative_stock = False
	engine.allow_zero_rate = True
	engine.stock_queue = [[10, 5]]
	engine.stock_value = 50
	engine.valuation_rate = 5
	engine.qty_by_dims = {engine._dim_key(engine.args): 10}
	engine.reserved_qty_by_dims = {}
	return engine


class TestReservationEnforcement(TestCase):
	def test_current_bucket_uses_preloaded_reservation_and_blocks_below_floor(self):
		engine = _engine(reserved_stock=6)

		with patch("yrp.stock.utils.get_sre_reserved_qty") as get_reserved:
			engine._validate_reservation_floor(engine.args, 6)
			get_reserved.assert_not_called()

		with (
			patch("yrp.stock.stock_ledger._", side_effect=lambda message: message),
			patch("yrp.stock.stock_ledger.frappe.throw", side_effect=_raise_frappe_error),
			self.assertRaisesRegex(NegativeStockError, "reserved quantity 6"),
		):
			engine._validate_reservation_floor(engine.args, 5)

	def test_sibling_tracking_bucket_reservation_is_loaded_once(self):
		engine = _engine(reserved_stock=2)
		sibling = frappe._dict(
			{
				"item": "ITEM-1",
				"warehouse": "WH-1",
				"lot": "LOT-1",
				"received_type": "Rejected",
			}
		)

		with patch("yrp.stock.utils.get_sre_reserved_qty", return_value=4) as get_reserved:
			self.assertEqual(engine._get_reserved_stock(sibling), 4)
			self.assertEqual(engine._get_reserved_stock(sibling), 4)

		get_reserved.assert_called_once_with(
			item_code="ITEM-1",
			warehouse="WH-1",
			for_update=True,
			lot="LOT-1",
			received_type="Rejected",
		)

	def test_valuation_bucket_query_ignores_tracking_dimensions(self):
		args = frappe._dict(
			{
				"item": "ITEM-1",
				"warehouse": "WH-1",
				"lot": "LOT-1",
				"received_type": "Accepted",
			}
		)

		with (
			patch("yrp.stock.stock_ledger.get_valuation_dimensions", return_value=["lot"]),
			patch("yrp.stock.utils.get_sre_reserved_qty", return_value=3) as get_reserved,
		):
			self.assertTrue(_valuation_bucket_has_active_reservation(args))

		get_reserved.assert_called_once_with(
			item_code="ITEM-1",
			warehouse="WH-1",
			for_update=True,
			lot="LOT-1",
		)

	def test_pure_valuation_replay_does_not_apply_current_reservation_to_history(self):
		engine = _engine(reserved_stock=None, validate_reserved_stock=0)
		outgoing = frappe._dict(
			{
				**engine.args,
				"name": "SLE-HISTORICAL",
				"voucher_type": 'YRP Stock Entry',
				"qty": -9,
				"outgoing_rate": 0,
			}
		)

		with (
			patch.object(engine, "_validate_reservation_floor") as validate,
			patch.object(engine, "_update_valuation_and_write"),
		):
			engine._process_sle(outgoing)

		validate.assert_not_called()

	def test_outgoing_replay_cannot_consume_current_reservation(self):
		engine = _engine(reserved_stock=9)
		outgoing = frappe._dict(
			{
				**engine.args,
				"name": "SLE-OUT",
				"voucher_type": 'YRP Stock Entry',
				"qty": -2,
				"outgoing_rate": 0,
			}
		)

		with (
			patch("yrp.stock.stock_ledger._", side_effect=lambda message: message),
			patch("yrp.stock.stock_ledger.frappe.throw", side_effect=_raise_frappe_error),
			self.assertRaises(NegativeStockError),
		):
			engine._process_sle(outgoing)

	def test_future_sibling_outgoing_is_checked_during_backdated_replay(self):
		engine = _engine(reserved_stock=0)
		sibling_key = ("LOT-1", "Rejected")
		engine.qty_by_dims[sibling_key] = 10
		future_outgoing = frappe._dict(
			{
				"item": "ITEM-1",
				"warehouse": "WH-1",
				"lot": "LOT-1",
				"received_type": "Rejected",
				"name": "SLE-FUTURE",
				"voucher_type": 'YRP Stock Entry',
				"qty": -3,
				"outgoing_rate": 0,
			}
		)

		with (
			patch("yrp.stock.utils.get_sre_reserved_qty", return_value=8),
			patch("yrp.stock.stock_ledger._", side_effect=lambda message: message),
			patch("yrp.stock.stock_ledger.frappe.throw", side_effect=_raise_frappe_error),
			self.assertRaises(NegativeStockError),
		):
			engine._process_sle(future_outgoing)

	def test_stock_reconciliation_cannot_set_balance_below_reservation(self):
		engine = _engine(reserved_stock=6)
		reconciliation = frappe._dict(
			{
				**engine.args,
				"name": "SLE-RECON",
				"voucher_type": 'YRP Stock Reconciliation',
				"qty": -5,
				"qty_after_transaction": 5,
				"rate": 5,
				"outgoing_rate": 0,
			}
		)

		with (
			patch("yrp.stock.stock_ledger._", side_effect=lambda message: message),
			patch("yrp.stock.stock_ledger.frappe.throw", side_effect=_raise_frappe_error),
			self.assertRaises(NegativeStockError),
		):
			engine._process_sle(reconciliation)

	def test_cancelling_last_incoming_checks_the_remaining_balance(self):
		engine = _engine(reserved_stock=6, is_cancelled=1)
		engine.qty_by_dims[engine._dim_key(engine.args)] = 5
		engine._init_previous = Mock()
		engine._get_entries_to_process = Mock(return_value=[])

		with (
			patch("yrp.stock.stock_ledger._", side_effect=lambda message: message),
			patch("yrp.stock.stock_ledger.frappe.throw", side_effect=_raise_frappe_error),
			self.assertRaises(NegativeStockError),
		):
			engine.run()

	def test_delivery_challan_consumes_own_reservation_before_ledger_posting(self):
		events = []
		doc = Mock()
		doc.update_work_order_deliverables.side_effect = lambda: events.append("deliverables")
		doc.update_work_order_reservations.side_effect = lambda: events.append("reservation")
		doc.make_stock_ledger_entries.side_effect = lambda: events.append("ledger")
		doc.make_repost_action.side_effect = lambda: events.append("repost")

		YRPDeliveryChallan.on_submit(doc)

		self.assertEqual(events, ["deliverables", "reservation", "ledger", "repost"])
