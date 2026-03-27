import { createApp } from 'vue';
import ProductionOrderTable from './ProductionOrderTable.vue';

export class ProductionOrderTableWrapper {

    constructor(wrapper) {
        this.$wrapper = $(wrapper);
        this.make_body();
    }

    make_body() {
        let $page_container = $('<div class="production-order-table frappe-control">').appendTo(this.$wrapper);
        this.app = createApp(ProductionOrderTable);
        this.component = this.app.mount(this.$wrapper.get(0));
    }

    set_settings(settings) {
        this.component.set_settings(settings);
    }

    set_edit(flag) {
        this.component.set_edit(flag);
    }

    get_final_output() {
        return this.component.get_final_output();
    }

    load_data(data) {
        this.component.load_data(data);
    }
};
