import AttributeValues from "./components/AttributeValues.vue";
import AttributeList from "./components/AttributeList.vue";
import DependentAttributeTemplate from "./components/DependentAttribute.vue";
import { EditBOMAttributeMappingWrapper, BOMAttributeMappingWrapper } from "./ItemBOM";
import { ProductionOrderTableWrapper } from "./ProductionOrder";
import { EditProcessMatrixWrapper } from "./ProcessMatrix";
import {
    LotOrderWrapper,
    OCRDetailWrapper,
} from "./Lot";
import CutPlanItems from "./CuttingPlan/components/CutPlanItems.vue";
import AlternativeDetail from "./Finishing/AlternativeDetail.vue";

import { createApp } from 'vue';

frappe.provide("frappe.production.ui");

function mount_component(component, wrapper) {
    const app = createApp(component);
    if (typeof SetVueGlobals === "function") SetVueGlobals(app);
    return {
        app,
        vue: app.mount($(wrapper).get(0)),
    };
}

frappe.production.ui.ItemAttributeValues = class {
    constructor({ wrapper, attr_values, attr_name } = {}) {
        this.$wrapper = $(wrapper);
        this.attr_values = attr_values;
        this.attr_name = attr_name;
        this.make_body();
    }
    make_body() {
        this.$page_container = $('<div class="attribute-value-template frappe-control">').appendTo(this.$wrapper);
        this.app = createApp(AttributeValues);
        this.app.mount(this.$wrapper.get(0));
    }
};

frappe.production.ui.ItemAttributeList = class {
    constructor({ wrapper, attr_values, attr_name } = {}) {
        this.$wrapper = $(wrapper);
        this.attr_values = attr_values;
        this.attr_name = attr_name;
        this.make_body();
    }
    make_body() {
        this.$page_container = $('<div class="attribute-list-template frappe-control">').appendTo(this.$wrapper);
        this.app = createApp(AttributeList);
        this.app.mount(this.$wrapper.get(0));
    }
};

frappe.production.ui.ItemDependentAttributeDetail = class {
    constructor(wrapper) {
        this.$wrapper = $(wrapper);
        this.make_body();
    }
    make_body() {
        this.$page_container = $('<div class="dependent-attribute-template frappe-control">').appendTo(this.$wrapper);
        this.app = createApp(DependentAttributeTemplate);
        this.app.mount(this.$wrapper.get(0));
    }
};

frappe.production.ui.BomItemAttributeMapping = BOMAttributeMappingWrapper;
frappe.production.ui.EditBOMAttributeMapping = EditBOMAttributeMappingWrapper;
frappe.production.ui.EditProcessMatrix = EditProcessMatrixWrapper;
frappe.production.ui.ProductionOrderTable = ProductionOrderTableWrapper;
frappe.production.ui.LotOrder = LotOrderWrapper;
frappe.production.ui.OCRDetail = OCRDetailWrapper;

frappe.production.ui.CutPlanItems = class {
    constructor(wrapper) {
        this.$wrapper = $(wrapper);
        this.make_app();
    }
    make_app() {
        const mounted = mount_component(CutPlanItems, this.$wrapper);
        this.app = mounted.app;
        this.vue = mounted.vue;
    }
    load_data(item_details, length) {
        this.vue.load_data(JSON.parse(JSON.stringify(item_details)));
        if (length > 0) {
            this.update_status();
        }
    }
    get_items() {
        return this.vue.get_items();
    }
    update_status() {
        this.vue.update_status();
    }
};

frappe.production.ui.AlternativeDetail = class {
    constructor(wrapper) {
        this.$wrapper = $(wrapper);
        this.make_app();
    }
    make_app() {
        const mounted = mount_component(AlternativeDetail, this.$wrapper);
        this.app = mounted.app;
        this.vue = mounted.vue;
    }
};
