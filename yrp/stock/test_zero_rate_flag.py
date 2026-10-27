"""Stock Entry rows marked Allow Zero Valuation Rate post at zero rate without a warning."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from yrp.stock import stock_ledger
from yrp.stock.stock_ledger import UpdateEntriesAfter
from yrp.stock.valuation import FIFOValuation

TICKED_ROWS = {"ROW-TICKED"}


def _sle(voucher_detail_no, voucher_type="YRP Stock Entry"):
	return frappe._dict(
		item="ITEM-1",
		warehouse="WH-1",
		voucher_type=voucher_type,
		voucher_detail_no=voucher_detail_no,
		qty=5,
		rate=0,
	)


def _row_flag(doctype, name, fieldname):
	return int(name in TICKED_ROWS)


class TestZeroRateFlag(TestCase):
	def _warned(self, sle, run_allows_zero_rate=False):
		engine = UpdateEntriesAfter.__new__(UpdateEntriesAfter)
		engine.allow_zero_rate = run_allows_zero_rate
		with (
			patch.object(stock_ledger.frappe, "get_meta") as get_meta,
			patch.object(stock_ledger.frappe.db, "get_value", side_effect=_row_flag),
			patch.object(stock_ledger.frappe, "msgprint") as msgprint,
		):
			get_meta.return_value.has_field.return_value = True
			engine._handle_incoming(sle, FIFOValuation([]), 0)
		return msgprint.called

	def test_each_replayed_row_uses_its_own_flag(self):
		# One run replays both rows of a mixed voucher; only the unticked row warns.
		self.assertFalse(self._warned(_sle("ROW-TICKED")))
		self.assertTrue(self._warned(_sle("ROW-UNTICKED")))

	def test_other_vouchers_still_warn(self):
		self.assertTrue(self._warned(_sle("ROW-TICKED", voucher_type="YRP Stock Update")))

	def test_run_level_allowance_still_silences(self):
		self.assertFalse(self._warned(_sle("ROW-UNTICKED"), run_allows_zero_rate=True))
