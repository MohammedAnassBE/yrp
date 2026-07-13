import './vue_plugins';
import {
    StockEntryWrapper,
    StockUpdateWrapper,
    StockReconciliationWrapper,
    EventBus,
} from './Stock';
import { WorkOrderItemEditorWrapper, CorrectionItemEditorWrapper } from './WorkOrder';
import { SuggestedBillDeliveryPersonWrapper } from './BillTracking';
import { InspectionEntryEditorWrapper } from './InspectionEntry';

frappe.provide('frappe.yrp');
frappe.provide('frappe.yrp.ui');
frappe.provide('frappe.yrp.stock');
frappe.provide('frappe.yrp.work_order');
frappe.provide('frappe.yrp.inspection');

frappe.yrp.ui.SuggestedBillDeliveryPerson = SuggestedBillDeliveryPersonWrapper;
frappe.yrp.stock.StockEntryItem = StockEntryWrapper;
frappe.yrp.stock.StockUpdateItem = StockUpdateWrapper;
frappe.yrp.stock.StockReconciliationItem = StockReconciliationWrapper;
frappe.yrp.work_order.ItemEditor = WorkOrderItemEditorWrapper;
frappe.yrp.work_order.CorrectionItemEditor = CorrectionItemEditorWrapper;
frappe.yrp.inspection.InspectionEditor = InspectionEntryEditorWrapper;
frappe.yrp.eventBus = EventBus;
