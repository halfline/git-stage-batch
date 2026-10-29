import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
const ERROR_DOMAIN = 'x.Error';

export class DbusService {
    constructor() {
        this._enabled = false;
        this._activityLog = [];
        this._onActivity = null;
        this._onEnabledChanged = null;
    }

    get enabled() { return this._enabled; }

    set enabled(value) {
        const next = !!value;
        if (next === this._enabled)
            return;
        this._enabled = next;
        if (this._onEnabledChanged)
            this._onEnabledChanged(next);
    }

    get activityLog() { return this._activityLog; }
    set onActivity(callback) { this._onActivity = callback; }
    set onEnabledChanged(callback) { this._onEnabledChanged = callback; }

    _logActivity(methodName, allowed = true) {
        const entry = {
            method: methodName,
            timestamp: new Date().toISOString(),
            allowed,
        };
        this._activityLog.push(entry);
        if (this._activityLog.length > 20)
            this._activityLog.shift();
        if (this._onActivity)
            this._onActivity(entry);
    }

    _checkEnabled(invocation, methodName) {
        if (!this._enabled) {
            this._logActivity(methodName, false);
            invocation.return_error_literal(
                Gio.DBusError,
                Gio.DBusError.FAILED,
                `${ERROR_DOMAIN}.Disabled: Automation is disabled`
            );
            return false;
        }
        this._logActivity(methodName);
        return true;
    }

    // --- Ungated methods ---

    PingAsync(_params, invocation) {
        this._logActivity('Ping');
        invocation.return_value(GLib.Variant.new('(b)', [true]));
    }

    GetEnabledAsync(_params, invocation) {
        this._logActivity('GetEnabled');
        invocation.return_value(GLib.Variant.new('(b)', [this._enabled]));
    }

    SetEnabledAsync([enabled], invocation) {
        // Enabling over D-Bus is refused on purpose. The session bus is shared
        // by every process running as this user, so an open SetEnabled(true)
        // would let any of them re-arm automation the user had switched off --
        // making the panel toggle advisory rather than a kill switch. Turning
        // automation OFF stays open to any caller: that direction is safe.
        if (enabled) {
            this._logActivity('SetEnabled', false);
            invocation.return_error_literal(
                Gio.DBusError,
                Gio.DBusError.ACCESS_DENIED,
                `${ERROR_DOMAIN}.AccessDenied: Automation can only be enabled `
                + 'from the Desktop Automation menu in the GNOME top bar'
            );
            return;
        }
        this._logActivity('SetEnabled');
        this.enabled = false;
        invocation.return_value(GLib.Variant.new('(b)', [true]));
    }

    // --- Screenshots ---

    ScreenshotAsync([includeCursor], invocation) {
        if (!this._checkEnabled(invocation, 'Screenshot')) return;
        Screenshot.screenshotFull(includeCursor, invocation);
    }

    ScreenshotWindowAsync([windowId, includeFrame, includeCursor], invocation) {
        if (!this._checkEnabled(invocation, 'ScreenshotWindow')) return;
        const win = Windows.findWindow(windowId);
        if (!win) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED,
                `${ERROR_DOMAIN}.WindowNotFound: No window with ID ${windowId}`);
            return;
        }
        const wasMinimized = win.minimized;
        if (wasMinimized) win.unminimize();
        win.activate(global.get_current_time());
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 150, () => {
            const onComplete = wasMinimized ? () => win.minimize() : null;
            Screenshot.screenshotWindow(includeCursor, includeFrame, invocation, onComplete);
            return GLib.SOURCE_REMOVE;
        });
    }

    ScreenshotAreaAsync([x, y, width, height, includeCursor], invocation) {
        if (!this._checkEnabled(invocation, 'ScreenshotArea')) return;
        Screenshot.screenshotArea(x, y, width, height, includeCursor, invocation);
    }

    PickColorAsync([x, y], invocation) {
        if (!this._checkEnabled(invocation, 'PickColor')) return;
        Screenshot.pickColor(x, y, invocation);
    }

    CleanupScreenshotsAsync(_params, invocation) {
        if (!this._checkEnabled(invocation, 'CleanupScreenshots')) return;
        try {
            const count = Screenshot.cleanupScreenshots();
            invocation.return_value(GLib.Variant.new('(u)', [count]));
        } catch (e) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, e.message);
        }
    }

    // --- Windows ---

}
