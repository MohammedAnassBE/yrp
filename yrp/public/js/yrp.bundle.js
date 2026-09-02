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

// Base YRP owns the generic Work Order close lifecycle. Customization apps may
// supply a fixed reason vocabulary, while a plain YRP installation keeps the
// reason as free text.
frappe.yrp.work_order.close_dialog_options =
    frappe.yrp.work_order.close_dialog_options || {};

frappe.yrp.work_order.get_close_reason_fields = function (defaults = {}) {
    const config = frappe.yrp.work_order.close_dialog_options || {};
    const reasonOptions = config.reason_options;
    const otherReasonValue = config.other_reason_value;
    const optionValues = Array.isArray(reasonOptions)
        ? reasonOptions
        : typeof reasonOptions === 'string'
            ? reasonOptions.split('\n')
            : [];
    const usesFixedReasons = optionValues.some((value) => value);
    const fields = [
        {
            fieldtype: usesFixedReasons ? 'Select' : 'Data',
            fieldname: 'close_reason',
            label: __('Close Reason'),
            reqd: 1,
            default: defaults.close_reason || '',
            ...(usesFixedReasons ? { options: reasonOptions } : {}),
        },
    ];

    if (usesFixedReasons && otherReasonValue && optionValues.includes(otherReasonValue)) {
        const otherReasonCondition = `eval: doc.close_reason == ${JSON.stringify(otherReasonValue)}`;
        fields.push({
            fieldtype: 'Data',
            fieldname: 'close_other_reason',
            label: __('Other Reason'),
            depends_on: otherReasonCondition,
            mandatory_depends_on: otherReasonCondition,
            default: defaults.close_other_reason || '',
        });
    }

    fields.push({
        fieldtype: 'Small Text',
        fieldname: 'close_remarks',
        label: __('Close Remarks'),
        default: defaults.close_remarks || '',
    });
    return fields;
};

frappe.yrp.work_order.open_close_dialog = function (frm, workOrder) {
    const factory = frm.doctype === 'YRP Purchase Invoice'
        ? frappe.yrp.work_order.make_purchase_invoice_close_dialog
        : frappe.yrp.work_order.make_work_order_close_dialog;
    if (typeof factory !== 'function') {
        frappe.throw(__('Work Order close dialog is unavailable.'));
    }
    return factory(frm, workOrder || frm.doc.name);
};
