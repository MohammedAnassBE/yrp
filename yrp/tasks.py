from yrp.yrp.doctype.yrp_item_price.yrp_item_price import update_all_expired_item_price
from yrp.yrp.doctype.yrp_process_cost.yrp_process_cost import update_all_expired_process_cost
from yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order import close_received_po


def daily():
	update_all_expired_item_price()
	update_all_expired_process_cost()
	close_received_po()
