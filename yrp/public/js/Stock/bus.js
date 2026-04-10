/**
 * Vue 3 EventBus replacement (ported from production_api).
 */
class Bus {
    constructor() {
        this.eventListeners = new Map();
    }

    registerEventListener(eventName, callback, once = false) {
        if (!this.eventListeners.has(eventName)) {
            this.eventListeners.set(eventName, []);
        }
        this.eventListeners.get(eventName).push({ callback, once });
    }

    $on(eventName, callback) {
        this.registerEventListener(eventName, callback);
    }

    $once(eventName, callback) {
        this.registerEventListener(eventName, callback, true);
    }

    $off(eventNameOrNames, callback = undefined) {
        const eventNames = Array.isArray(eventNameOrNames) ? eventNameOrNames : [eventNameOrNames];
        for (const eventName of eventNames) {
            const listeners = this.eventListeners.get(eventName);
            if (listeners === undefined) continue;
            if (typeof callback === "function") {
                for (let i = listeners.length - 1; i >= 0; i--) {
                    if (listeners[i].callback === callback) listeners.splice(i, 1);
                }
            } else {
                this.eventListeners.delete(eventName);
            }
        }
    }

    $emit(eventName, ...args) {
        if (!this.eventListeners.has(eventName)) return;
        const listeners = this.eventListeners.get(eventName);
        const toDelete = [];
        for (const [i, l] of listeners.entries()) {
            l.callback(...args);
            if (l.once) toDelete.push(i);
        }
        for (let i = toDelete.length - 1; i >= 0; i--) listeners.splice(toDelete[i], 1);
    }
}

const EventBus = new Bus();
export default EventBus;
