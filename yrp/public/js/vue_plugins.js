import AttributeValues from "./components/AttributeValues.vue";
import AttributeList from "./components/AttributeList.vue";
import DependentAttributeTemplate from "./components/DependentAttribute.vue";
import { EditBOMAttributeMappingWrapper, BOMAttributeMappingWrapper } from "./ItemBOM";
import { ProductionOrderTableWrapper } from "./ProductionOrder";

import { createApp } from 'vue';

frappe.provide("frappe.production.ui");

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
frappe.production.ui.ProductionOrderTable = ProductionOrderTableWrapper;
