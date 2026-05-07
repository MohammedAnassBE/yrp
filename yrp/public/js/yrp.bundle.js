import './vue_plugins';
import {
    StockEntryWrapper,
    StockUpdateWrapper,
    StockReconciliationWrapper,
    EventBus,
} from './Stock';
import { WorkOrderItemEditorWrapper } from './WorkOrder';

frappe.provide('frappe.yrp');
frappe.provide('frappe.yrp.stock');
frappe.provide('frappe.yrp.work_order');

frappe.yrp.stock.StockEntryItem = StockEntryWrapper;
frappe.yrp.stock.StockUpdateItem = StockUpdateWrapper;
frappe.yrp.stock.StockReconciliationItem = StockReconciliationWrapper;
frappe.yrp.work_order.ItemEditor = WorkOrderItemEditorWrapper;
frappe.yrp.eventBus = EventBus;
