/*
 * PMCA Camera Utility - web frontend (Preact via embedded htm/preact standalone).
 *
 * The Python backend (pmca-web.py) pushes events into the page by calling the
 * globals window._onEvent / window._appendLog / window._signalError. Those are
 * wired to a tiny event bus that components subscribe to with the useBus() hook.
 */
const { html, render, createContext, useState, useEffect, useRef, useCallback, useContext } = htmPreact;

/* ------------------------------------------------------------------ *
 * Event bus + Python -> JS bridge
 * ------------------------------------------------------------------ */
const bus = (() => {
    const listeners = {};
    return {
        on(event, cb) {
            (listeners[event] || (listeners[event] = [])).push(cb);
            return () => { listeners[event] = listeners[event].filter((f) => f !== cb); };
        },
        emit(event, data) {
            (listeners[event] || []).forEach((cb) => cb(data));
        },
    };
})();

window._onEvent = (event, data) => bus.emit(event, data);
window._appendLog = (text) => bus.emit('log', text);
window._signalError = () => bus.emit('error');

/** Subscribe to a bus event for the lifetime of the component. */
function useBus(event, handler) {
    const ref = useRef(handler);
    ref.current = handler;
    useEffect(() => bus.on(event, (data) => ref.current(data)), [event]);
}

/* ------------------------------------------------------------------ *
 * Python API wrapper
 * ------------------------------------------------------------------ */
const call = (name, ...args) =>
    (window.pywebview && pywebview.api && pywebview.api[name])
        ? pywebview.api[name](...args)
        : Promise.resolve();

const api = {
    getConfig: () => call('get_config'),
    loadApps: () => call('load_apps'),
    getInfo: () => call('get_info'),
    installApp: (pkg) => call('install_app', pkg),
    selectApk: () => call('select_apk'),
    installApk: () => call('install_apk'),
    firmwareUpdate: () => call('firmware_update'),
    startTweaksUpdater: () => call('start_tweaks_updater'),
    startTweaksService: () => call('start_tweaks_service'),
    setTweak: (id, enabled) => call('set_tweak', id, enabled),
    applyTweaks: () => call('apply_tweaks'),
    cancelTweaks: () => call('cancel_tweaks'),
    readWifi: (multi) => call('read_wifi', multi),
    writeWifi: (nets, multi) => call('write_wifi', nets, multi),
    downloadBackup: (mode, parsed) => call('download_backup', mode, parsed),
    restoreBackup: (mode) => call('restore_backup', mode),
    pluginCall: (id, method, ...args) => call('plugin_call', id, method, ...args),
};

function onReady(cb) {
    if (window.pywebview && window.pywebview.api) cb();
    else window.addEventListener('pywebviewready', cb, { once: true });
}

function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    }
    fallbackCopy(text);
    return Promise.resolve();
}

function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
}

/* ------------------------------------------------------------------ *
 * Shared app context (busy state, toast, confirm dialog)
 * ------------------------------------------------------------------ */
const AppCtx = createContext(null);
const useApp = () => useContext(AppCtx);

/* ------------------------------------------------------------------ *
 * Reusable UI bits
 * ------------------------------------------------------------------ */
const Card = ({ title, children }) => html`
    <div class="card">
        ${title && html`<div class="card-title">${title}</div>`}
        ${children}
    </div>`;

const Icon = ({ path }) => html`
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        ${path}
    </svg>`;

const ModeSelect = ({ value, onChange, disabled }) => html`
    <span class="field-label">in</span>
    <select class="select-inline" value=${value} disabled=${disabled}
            onChange=${(e) => onChange(e.target.value)}>
        <option value="service">service</option>
        <option value="updater">updater</option>
    </select>
    <span class="field-label">mode</span>`;

/* ------------------------------------------------------------------ *
 * Camera tab
 * ------------------------------------------------------------------ */
const INFO_FIELDS = ['Model', 'Product code', 'Serial number', 'Firmware version', 'Lens', 'GPS Data'];
const INFO_OPTIONAL = ['Lens', 'GPS Data'];

function CameraInfoCard() {
    const { busy, showToast } = useApp();
    const [info, setInfo] = useState(null);

    useBus('camera_info', (data) => {
        const map = {};
        (data || []).forEach((row) => { map[row.key] = row.value; });
        setInfo(map);
    });

    const fields = info
        ? INFO_FIELDS.filter((f) => info[f] !== undefined)
        : INFO_FIELDS.filter((f) => !INFO_OPTIONAL.includes(f));

    const copy = (value) => copyText(value).then(() => showToast('Copied: ' + value));

    return html`
        <${Card}>
            <button class="btn-primary btn-block" disabled=${busy.info} onClick=${api.getInfo}>
                Get camera info
            </button>
            <table class="camera-info-table mt-12">
                ${fields.map((field) => html`
                    <tr key=${field}>
                        <td>${field}</td>
                        ${info
                            ? html`<td class="camera-info-value copyable" onClick=${() => copy(info[field])}>${info[field]}</td>`
                            : html`<td class="camera-info-value camera-info-placeholder">—</td>`}
                    </tr>`)}
            </table>
        <//>`;
}

const KEY_TYPES = ['None', 'WEP', 'WPA/WPA2'];

function WifiCard() {
    const { busy, showToast } = useApp();
    const [multi, setMulti] = useState(false);
    const [networks, setNetworks] = useState(null);
    const [error, setError] = useState(null);

    useBus('wifi_result', (data) => {
        if (data.error) { setError(data.error); setNetworks(null); }
        else { setError(null); setNetworks((data.networks || []).map((n) => ({ ...n }))); }
    });
    useBus('wifi_write_result', (data) =>
        showToast(data.error || 'WiFi settings written successfully'));

    const update = (i, field, val) =>
        setNetworks((ns) => ns.map((n, idx) => (idx === i ? { ...n, [field]: val } : n)));
    const remove = (i) => setNetworks((ns) => ns.filter((_, idx) => idx !== i));
    const add = () => setNetworks((ns) => [...(ns || []), { sid: '', key: '', keyType: 0 }]);

    const write = () => {
        const valid = networks.filter((n) => n.sid).map((n) => ({
            sid: n.sid, key: n.key || '', keyType: Number(n.keyType),
        }));
        api.writeWifi(valid, multi);
    };

    return html`
        <${Card} title="WiFi Settings">
            <div class="input-row mb-12">
                <label class="check-inline">
                    <input type="checkbox" checked=${multi} onChange=${(e) => setMulti(e.target.checked)}/>
                    Multi-WiFi
                </label>
                <button class="btn-primary push-right" disabled=${busy.wifi}
                        onClick=${() => api.readWifi(multi)}>Read</button>
            </div>

            ${error && html`<div class="muted text-danger">${error}</div>`}
            ${!error && networks === null && html`
                <div class="muted">Press "Read" to load WiFi networks from camera</div>`}
            ${!error && networks !== null && networks.length === 0 && html`
                <div class="muted">No WiFi networks stored on camera</div>`}

            ${networks && networks.map((net, i) => html`
                <div class="wifi-entry" key=${i}>
                    <div class="input-row">
                        <label>SSID</label>
                        <input type="text" value=${net.sid} placeholder="Network name"
                               onInput=${(e) => update(i, 'sid', e.target.value)}/>
                        <button class="icon-btn" title="Remove" onClick=${() => remove(i)}>
                            <${Icon} path=${html`<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>`}/>
                        </button>
                    </div>
                    <div class="input-row">
                        <label>Key</label>
                        <input type="text" value=${net.key} placeholder="Password"
                               onInput=${(e) => update(i, 'key', e.target.value)}/>
                    </div>
                    <div class="input-row">
                        <label>Type</label>
                        <select class="select-inline" value=${net.keyType}
                                onChange=${(e) => update(i, 'keyType', Number(e.target.value))}>
                            ${KEY_TYPES.map((label, k) => html`<option key=${k} value=${k}>${label}</option>`)}
                        </select>
                    </div>
                </div>`)}

            ${networks !== null && html`
                <div class="input-row mt-12">
                    <button onClick=${add}>+ Add network</button>
                    <button class="btn-primary push-right" disabled=${busy.wifi} onClick=${write}>
                        Write to camera
                    </button>
                </div>`}
        <//>`;
}

function BackupCard() {
    const { busy, confirm } = useApp();
    const [mode, setMode] = useState('service');
    const [parsed, setParsed] = useState(false);
    const [status, setStatus] = useState(null);

    useBus('backup_status', (data) => {
        setStatus(data.message || '');
        if (data.done) setTimeout(() => setStatus(null), 5000);
    });

    const restore = () => confirm({
        title: 'Restore Backup',
        message: 'This will overwrite ALL camera settings. This action cannot be undone.',
        confirmLabel: 'Restore',
        onConfirm: () => api.restoreBackup(mode),
    });

    return html`
        <${Card} title="Backup Settings">
            <div class="warning mb-12">
                Puts camera in <strong>updater</strong> or <strong>service</strong> mode.
                Restoring a backup will <strong>overwrite all camera settings</strong>.
            </div>
            <div class="input-row">
                <button class="btn-primary" disabled=${busy.backup}
                        onClick=${() => api.downloadBackup(mode, parsed)}>Download backup</button>
                <button class="btn-danger" disabled=${busy.backup} onClick=${restore}>Restore backup...</button>
                <${ModeSelect} value=${mode} onChange=${setMode}/>
            </div>
            <label class="check-inline mt-8">
                <input type="checkbox" checked=${parsed} onChange=${(e) => setParsed(e.target.checked)}/>
                Get backup as parsed text
            </label>
            ${status !== null && html`<div class="muted mt-8">${status}</div>`}
        <//>`;
}

function FirmwareCard() {
    const { busy } = useApp();
    return html`
        <${Card} title="Firmware update">
            <button class="btn-primary btn-block" disabled=${busy.firmware} onClick=${api.firmwareUpdate}>
                Select firmware file and update...
            </button>
        <//>`;
}

const CameraTab = () => html`
    <${CameraInfoCard}/>
    <${WifiCard}/>
    <${BackupCard}/>
    <${FirmwareCard}/>`;

/* ------------------------------------------------------------------ *
 * Install tab
 * ------------------------------------------------------------------ */
function InstallTab({ config }) {
    const { busy } = useApp();
    const [apps, setApps] = useState([]);
    const [selected, setSelected] = useState('');
    const [apkName, setApkName] = useState(null);
    const [refreshing, setRefreshing] = useState(false);

    useBus('apps_loading', () => setRefreshing(true));
    useBus('apps_loaded', (data) => { setApps(data || []); setRefreshing(false); });
    useBus('apk_selected', (name) => { setApkName(name); setSelected(''); });

    const pickApp = (pkg) => { setSelected(pkg); if (pkg) setApkName(null); };

    let label = 'Select App to Install';
    let action = null;
    if (selected) {
        const app = apps.find((a) => a.package === selected);
        label = 'Install ' + (app ? app.name : selected);
        action = () => api.installApp(selected);
    } else if (apkName) {
        label = 'Install ' + apkName.replace(/\.apk$/i, '');
        action = api.installApk;
    }

    const source = config.githubAppListUser
        ? 'https://github.com/' + config.githubAppListUser + '/' + config.githubAppListRepo
        : '#';

    return html`
        <${Card} title="Select an app from the list">
            <div class="input-row">
                <select value=${selected} onChange=${(e) => pickApp(e.target.value)}>
                    <option value="">${refreshing ? 'Loading...' : '-- Select an app --'}</option>
                    ${apps.map((a) => html`<option key=${a.package} value=${a.package}>${a.name}</option>`)}
                </select>
                <button disabled=${busy.install || refreshing} onClick=${api.loadApps}>Refresh</button>
            </div>
            <div class="mt-8"><a class="source-link" href=${source} target="_blank">Source</a></div>
        <//>
        <${Card} title="Or install from APK file">
            <div class="input-row">
                <span class="muted grow">${apkName || 'No file selected'}</span>
                <button onClick=${api.selectApk}>Select APK...</button>
            </div>
        <//>
        <${Card}>
            <button class="btn-primary btn-block" disabled=${!action || busy.install}
                    onClick=${() => action && action()}>${label}</button>
        <//>`;
}

/* ------------------------------------------------------------------ *
 * Tweaks tab
 * ------------------------------------------------------------------ */
const TWEAK_PLACEHOLDERS = [
    'Disable video recording limit',
    'Disable 4K video recording limit',
    'Unlock all languages',
    'Enable PAL / NTSC selector & warning',
    'PAL / NTSC',
    'Enable USB app installer',
];

function TweaksTab() {
    const { busy } = useApp();
    const [mode, setMode] = useState('service');
    const [tweaks, setTweaks] = useState(null);
    const [applying, setApplying] = useState(false);

    useBus('tweaks_available', (data) => { setTweaks(data); setApplying(false); });
    useBus('tweaks_applying', () => setApplying(true));
    useBus('tweaks_done', () => { setTweaks(null); setApplying(false); });

    const active = tweaks !== null;
    const start = () => (mode === 'updater' ? api.startTweaksUpdater() : api.startTweaksService());

    return html`
        <${Card}>
            <div class="input-row">
                <button class="btn-primary nowrap" disabled=${busy.tweaks || active} onClick=${start}>
                    Start tweaking
                </button>
                <${ModeSelect} value=${mode} onChange=${setMode} disabled=${active}/>
            </div>
        <//>
        <${Card} title="Tweaks">
            ${!active
                ? TWEAK_PLACEHOLDERS.map((desc, i) => html`
                    <label class="tweak-item tweaks-placeholder" key=${i}>
                        <input type="checkbox" disabled/>
                        <div class="tweak-info">
                            <div class="tweak-desc">${desc}</div>
                            <div class="tweak-value">Connect camera to see status</div>
                        </div>
                    </label>`)
                : tweaks.map((t) => html`
                    <label class="tweak-item" key=${t.id}>
                        <input type="checkbox" checked=${t.enabled} disabled=${applying}
                               onChange=${(e) => api.setTweak(t.id, e.target.checked)}/>
                        <div class="tweak-info">
                            <div class="tweak-desc">${t.desc}</div>
                            <div class="tweak-value">${t.value}</div>
                        </div>
                    </label>`)}
            <div class="btn-group mt-12">
                <button disabled=${!active || applying} onClick=${api.cancelTweaks}>Disconnect</button>
                <button class="btn-primary" disabled=${!active || applying} onClick=${api.applyTweaks}>Apply</button>
            </div>
        <//>`;
}

/* ------------------------------------------------------------------ *
 * Log panel
 * ------------------------------------------------------------------ */
function Log() {
    const { showToast } = useApp();
    const [text, setText] = useState('');
    const [expanded, setExpanded] = useState(true);
    const [hasError, setHasError] = useState(false);
    const bodyRef = useRef(null);

    useBus('log', (chunk) => setText((prev) => prev + chunk));
    useBus('error', () => setHasError(true));
    useBus('task_start', () => setHasError(false));

    useEffect(() => {
        if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }, [text]);

    const toggle = () => {
        setExpanded((e) => !e);
        if (!expanded) setHasError(false);
    };
    const copy = () => copyText(text).then(() => showToast('Copied to clipboard'));
    const clear = () => { setText(''); setHasError(false); };

    const cls = 'log-container' + (expanded ? ' expanded' : '') + (hasError ? ' has-error' : '');
    return html`
        <div class=${cls}>
            <div class="log-header">
                <span class="log-toggle" onClick=${toggle}>
                    <${Icon} path=${html`<polyline points="9 18 15 12 9 6"/>`}/>
                    Log
                    <span class="log-error-dot"></span>
                </span>
                <div class="log-actions">
                    <button title="Copy log" onClick=${copy}>
                        <${Icon} path=${html`<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>`}/>
                    </button>
                    <button title="Clear log" onClick=${clear}>
                        <${Icon} path=${html`<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>`}/>
                    </button>
                </div>
            </div>
            <div id="log" ref=${bodyRef} onClick=${() => setHasError(false)}>${text}</div>
        </div>`;
}

/* ------------------------------------------------------------------ *
 * Toast + confirm modal
 * ------------------------------------------------------------------ */
const Toast = ({ toast }) => html`
    <div class="toast ${toast.visible ? 'visible' : ''}">${toast.msg}</div>`;

const ConfirmModal = ({ opts, onClose }) => html`
    <div class="modal-overlay">
        <div class="modal-box">
            <img src="icon.png" alt="" class="modal-icon"/>
            <div class="modal-title">${opts.title}</div>
            <div class="modal-message">${opts.message}</div>
            <div class="btn-group modal-actions">
                <button onClick=${() => onClose(false)}>Cancel</button>
                <button class="btn-danger" onClick=${() => onClose(true)}>${opts.confirmLabel || 'OK'}</button>
            </div>
        </div>
    </div>`;

/* ------------------------------------------------------------------ *
 * Header + tabs + root
 * ------------------------------------------------------------------ */
/* Representative light-outline icons (Feather/Lucide style, stroke-only). */
const TAB_ICONS = {
    info: html`<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>`,
    install: html`<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>`,
    tweaks: html`<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>`,
};

const CORE_TABS = [
    { id: 'info', label: 'Camera', component: CameraTab, icon: TAB_ICONS.info, order: 10 },
    { id: 'install', label: 'Install App', component: InstallTab, icon: TAB_ICONS.install, order: 20 },
    { id: 'tweaks', label: 'Tweaks', component: TweaksTab, icon: TAB_ICONS.tweaks, order: 30 },
];

/** Optional plugins register tabs here after the backend reports them. */
const pluginTabRegistry = [];
let pluginTabListener = null;

window.PMCA = {
    html,
    useState,
    useEffect,
    useRef,
    useCallback,
    useContext,
    useApp,
    useBus,
    Card,
    Icon,
    api,
    copyText,
    registerTab(tab) {
        if (!tab || !tab.id || !tab.component) return;
        const entry = {
            id: tab.id,
            label: tab.label || tab.id,
            component: tab.component,
            icon: tab.icon || null,
            order: tab.order != null ? tab.order : 40,
        };
        const idx = pluginTabRegistry.findIndex((t) => t.id === entry.id);
        if (idx >= 0) pluginTabRegistry[idx] = entry;
        else pluginTabRegistry.push(entry);
        if (pluginTabListener) pluginTabListener(pluginTabRegistry.slice());
    },
};

function loadPluginScripts(plugins) {
    (plugins || []).forEach((plugin) => {
        if (!plugin || !plugin.js) return;
        try {
            // Trusted local plugin scripts shipped with the app / drop-in package.
            // eslint-disable-next-line no-new-func
            Function(plugin.js)();
        } catch (err) {
            console.error('Failed to load plugin', plugin.id, err);
        }
    });
}

function App() {
    const [config, setConfig] = useState({ version: '', docsUrl: '', githubAppListUser: '', githubAppListRepo: '' });
    const [tabs, setTabs] = useState(CORE_TABS);
    const [tab, setTab] = useState('info');
    const [busy, setBusy] = useState({});
    const [toast, setToast] = useState({ msg: '', visible: false });
    const [confirmOpts, setConfirmOpts] = useState(null);
    const toastTimer = useRef(null);

    useBus('task_start', (type) => setBusy((b) => ({ ...b, [type]: true })));
    useBus('task_end', (type) => setBusy((b) => ({ ...b, [type]: false })));

    const showToast = useCallback((msg) => {
        setToast({ msg, visible: true });
        clearTimeout(toastTimer.current);
        toastTimer.current = setTimeout(() => setToast((t) => ({ ...t, visible: false })), 3000);
    }, []);

    const confirm = useCallback((opts) => setConfirmOpts(opts), []);

    const closeConfirm = (ok) => {
        if (ok && confirmOpts) confirmOpts.onConfirm();
        setConfirmOpts(null);
    };

    useEffect(() => {
        const mergeTabs = (plugins) => {
            const merged = CORE_TABS.concat(plugins || []).slice().sort((a, b) => a.order - b.order);
            setTabs(merged);
        };
        pluginTabListener = mergeTabs;
        mergeTabs(pluginTabRegistry);
        return () => { pluginTabListener = null; };
    }, []);

    useEffect(() => onReady(() => {
        api.getConfig().then((cfg) => {
            if (cfg) {
                setConfig(cfg);
                loadPluginScripts(cfg.plugins);
            }
        }).catch(() => {});
        api.loadApps();
    }), []);

    const title = 'PMCA Camera Utility' + (config.version ? ' ' + config.version : '');
    const docsUrl = config.docsUrl ? config.docsUrl + '/devices.html' : '#';

    return html`
        <${AppCtx.Provider} value=${{ busy, showToast, confirm }}>
            <div class="header">
                <h1>${title}</h1>
                <a href=${docsUrl} target="_blank">Camera compatibility</a>
            </div>

            <div class="main-content">
                <div class="tabs" role="tablist">
                    ${tabs.map(({ id, label, icon }) => html`
                        <button class="tab ${tab === id ? 'active' : ''}" key=${id}
                                type="button" role="tab" aria-selected=${tab === id}
                                onClick=${() => setTab(id)}>
                            <span class="tab-icon"><${Icon} path=${icon}/></span>
                            <span class="tab-label">${label}</span>
                        </button>`)}
                </div>
                <div class="tab-panels">
                    ${tabs.map(({ id, component: Comp }) => html`
                        <div class="tab-panel ${tab === id ? 'active' : ''}" key=${id}>
                            <${Comp} config=${config}/>
                        </div>`)}
                </div>
            </div>

            <${Log}/>
            <${Toast} toast=${toast}/>
            ${confirmOpts && html`<${ConfirmModal} opts=${confirmOpts} onClose=${closeConfirm}/>`}
        <//>`;
}

render(html`<${App}/>`, document.getElementById('app'));
