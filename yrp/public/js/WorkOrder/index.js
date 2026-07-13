import { createApp } from 'vue';
import WorkOrderItemEditor from './WorkOrderItemEditor.vue';
import CorrectionItemEditor from './CorrectionItemEditor.vue';

function _mount(component, wrapper, options) {
    const $wrapper = $(wrapper);
    $('<div class="item frappe-control">').appendTo($wrapper);
    const app = createApp(component, options || {});
    if (typeof SetVueGlobals === 'function') SetVueGlobals(app);
    const inst = app.mount($wrapper.get(0));
    return { app, inst };
}

export class WorkOrderItemEditorWrapper {
    constructor(wrapper, options) {
        const { app, inst } = _mount(WorkOrderItemEditor, wrapper, options);
        this.app = app;
        this.editor = inst;
    }
    get_items() { return this.editor.get_items(); }
    load_data(data) { this.editor.load_data(data); }
    update_status() { this.editor.update_status(); }
}

export class CorrectionItemEditorWrapper {
    constructor(wrapper, options) {
        const { app, inst } = _mount(CorrectionItemEditor, wrapper, options);
        this.app = app;
        this.editor = inst;
    }
    get_items() { return this.editor.get_items(); }
    load_data(data) { this.editor.load_data(data); }
    update_status() { this.editor.update_status(); }
}
