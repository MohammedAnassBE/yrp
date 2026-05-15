from yrp.yrp.doctype.item_price.item_price import update_all_expired_item_price
from yrp.yrp.doctype.process_cost.process_cost import update_all_expired_process_cost
from yrp.yrp.doctype.purchase_order.purchase_order import close_received_po


def daily():
	update_all_expired_item_price()
	update_all_expired_process_cost()
	close_received_po()
