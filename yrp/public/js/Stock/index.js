import { createApp } from 'vue';
import StockEntry from './StockEntry/StockEntry.vue';
import StockUpdate from './StockUpdate/StockUpdate.vue';
import StockReconciliation from './StockReconciliation/StockReconciliation.vue';
import EventBus from './bus.js';

function _mount(wrapper, Component) {
    const $wrapper = $(wrapper);
    $('<div class="item frappe-control">').appendTo($wrapper);
    const app = createApp(Component);
    if (typeof SetVueGlobals === 'function') SetVueGlobals(app);
    const inst = app.mount($wrapper.get(0));
    return { app, inst };
}

export class StockEntryWrapper {
    constructor(wrapper) {
        const { app, inst } = _mount(wrapper, StockEntry);
        this.app = app;
        this.stockEntry = inst;
    }
    get_items() { return this.stockEntry.get_items(); }
    load_data(data) { this.stockEntry.load_data(data); }
    update_status() { this.stockEntry.update_status(); }
}

export class StockUpdateWrapper {
    constructor(wrapper) {
        const { app, inst } = _mount(wrapper, StockUpdate);
        this.app = app;
        this.stockUpdate = inst;
    }
    get_items() { return this.stockUpdate.get_items(); }
    load_data(data) { this.stockUpdate.load_data(data); }
    update_status() { this.stockUpdate.update_status(); }
}

export class StockReconciliationWrapper {
    constructor(wrapper) {
        const { app, inst } = _mount(wrapper, StockReconciliation);
        this.app = app;
        this.stockReconciliation = inst;
    }
    get_items() { return this.stockReconciliation.get_items(); }
    load_data(data) { this.stockReconciliation.load_data(data); }
    update_status() { this.stockReconciliation.update_status(); }
}

export { EventBus };
