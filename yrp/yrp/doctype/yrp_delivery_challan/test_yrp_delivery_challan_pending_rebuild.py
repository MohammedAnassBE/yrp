from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import UnitTestCase

from yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan import (
	rebuild_work_order_deliverable_pending,
)


class TestDeliveryChallanPendingRebuild(UnitTestCase):
	def test_rebuild_uses_submitted_deliveries_less_returns(self):
		deliverable = frappe._dict(name="DEL-1", qty=10, pending_quantity=99)

		deliverable.db_set = MagicMock()
		work_order = frappe._dict(name="WO-1", deliverables=[deliverable])

		def sql(query, *_args, **_kwargs):
			if "FROM `tabYRP Delivery Challan Item`" in query:
				return [frappe._dict(ref_docname="DEL-1", quantity=8)]
			if "FROM `tabYRP Goods Received Note Item`" in query:
				return [frappe._dict(ref_docname="DEL-1", quantity=2)]
			return []

		with (
			patch("yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan.frappe.db.sql", side_effect=sql),
			patch("yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan.frappe.get_doc", return_value=work_order),
			patch("yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan._update_work_order_status"),
		):
			result = rebuild_work_order_deliverable_pending("WO-1")

		deliverable.db_set.assert_called_once()
		self.assertEqual(result["pending_quantity"], 4)
