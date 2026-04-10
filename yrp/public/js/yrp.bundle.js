import './vue_plugins';
import {
    StockEntryWrapper,
    StockUpdateWrapper,
    StockReconciliationWrapper,
    EventBus,
} from './Stock';

frappe.provide('frappe.yrp');
frappe.provide('frappe.yrp.stock');

frappe.yrp.stock.StockEntryItem = StockEntryWrapper;
frappe.yrp.stock.StockUpdateItem = StockUpdateWrapper;
frappe.yrp.stock.StockReconciliationItem = StockReconciliationWrapper;
frappe.yrp.eventBus = EventBus;
