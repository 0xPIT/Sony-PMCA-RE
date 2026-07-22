var api = {
    getInfo: function() { pywebview.api.get_info(); },
    loadApps: function() { pywebview.api.load_apps(); },
    installApp: function(pkg) { pywebview.api.install_app(pkg); },
    selectApk: function() { pywebview.api.select_apk(); },
    installApk: function() { pywebview.api.install_apk(); },
    firmwareUpdate: function() { pywebview.api.firmware_update(); },
    startTweaksUpdater: function() { pywebview.api.start_tweaks_updater(); },
    startTweaksService: function() { pywebview.api.start_tweaks_service(); },
    setTweak: function(id, enabled) { pywebview.api.set_tweak(id, enabled); },
    applyTweaks: function() { pywebview.api.apply_tweaks(); },
    cancelTweaks: function() { pywebview.api.cancel_tweaks(); },
    readWifi: function(multi) { pywebview.api.read_wifi(multi); },
    writeWifi: function(networks, multi) { pywebview.api.write_wifi(networks, multi); },
    runDiagnostics: function() { pywebview.api.run_diagnostics(); },
    downloadBackup: function(mode, parsedtextproperties) { pywebview.api.download_backup(mode, parsedtextproperties); },
    restoreBackup: function(mode) { pywebview.api.restore_backup(mode); },
};

var logEl = document.getElementById('log');

var logContainer = document.getElementById('log-container');

window._appendLog = function(text) {
    logEl.textContent += text;
    logEl.scrollTop = logEl.scrollHeight;
};

logEl.addEventListener('click', function() {
    logContainer.classList.remove('has-error');
});

window._signalError = function() {
    logContainer.classList.add('has-error');
};

function toggleLog() {
    logContainer.classList.toggle('expanded');
    if (logContainer.classList.contains('expanded')) {
        logContainer.classList.remove('has-error');
    }
}

function clearLog() {
    logEl.textContent = '';
    logContainer.classList.remove('has-error');
}

function copyLog() {
    var text = logEl.textContent;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() { showToast('Copied to clipboard'); }).catch(function() { copyFallback(text); showToast('Copied to clipboard'); });
    } else {
        copyFallback(text);
        showToast('Copied to clipboard');
    }
}

function showToast(msg) {
    var el = document.getElementById('toast');
    el.textContent = msg;
    el.classList.add('visible');
    clearTimeout(el._timeout);
    el._timeout = setTimeout(function() { el.classList.remove('visible'); }, 3000);
}

function copyFallback(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
}

window._onEvent = function(event, data) {
    switch (event) {
        case 'camera_info':
            var cells = document.querySelectorAll('#camera-info-display .camera-info-value');
            var dataMap = {};
            if (data && data.length) {
                for (var i = 0; i < data.length; i++) {
                    dataMap[data[i].key] = data[i].value;
                }
            }
            for (var i = 0; i < cells.length; i++) {
                var field = cells[i].getAttribute('data-field');
                if (dataMap[field] !== undefined) {
                    cells[i].textContent = dataMap[field];
                    cells[i].classList.remove('camera-info-placeholder');
                    cells[i].classList.add('copyable');
                    cells[i].closest('tr').style.display = '';
                } else {
                    cells[i].textContent = '—';
                    cells[i].classList.add('camera-info-placeholder');
                    cells[i].classList.remove('copyable');
                    cells[i].closest('tr').style.display = 'none';
                }
            }
            break;

        case 'apk_selected':
            selectedApk = true;
            document.getElementById('apk-filename').textContent = data;
            document.getElementById('app-select').value = '';
            updateInstallButton();
            break;

        case 'apps_loading':
            document.getElementById('btn-refresh-apps').disabled = true;
            break;

        case 'apps_loaded':
            document.getElementById('btn-refresh-apps').disabled = false;
            var select = document.getElementById('app-select');
            select.innerHTML = '<option value="">-- Select an app --</option>';
            if (data && data.length) {
                for (var i = 0; i < data.length; i++) {
                    var opt = document.createElement('option');
                    opt.value = data[i].package;
                    opt.textContent = data[i].name;
                    select.appendChild(opt);
                }
            }
            updateInstallButton();
            break;

        case 'task_start':
            logContainer.classList.remove('has-error');
            setButtonsDisabled(data, true);
            break;

        case 'task_end':
            setButtonsDisabled(data, false);
            break;

        case 'wifi_result':
            if (data.error) {
                document.getElementById('wifi-list').innerHTML =
                    '<div style="font-size:13px;color:var(--color-danger);">' + escapeHtml(data.error) + '</div>';
            } else {
                renderWifiNetworks(data.networks);
            }
            break;

        case 'wifi_write_result':
            if (data.error) {
                showToast(data.error);
            } else {
                showToast('WiFi settings written successfully');
            }
            break;

        case 'tweaks_available':
            showTweaksModal(data);
            break;

        case 'tweaks_applying':
            setTweaksModalDisabled(true);
            break;

        case 'tweaks_done':
            hideTweaksModal();
            break;

        case 'diagnostics_result':
            renderDiagnostics(data);
            break;

        case 'backup_status':
            var el = document.getElementById('backup-status');
            el.style.display = 'block';
            el.textContent = data.message || '';
            if (data.done) {
                setTimeout(function() { el.style.display = 'none'; }, 5000);
            }
            break;
    }
};

function setButtonsDisabled(taskType, disabled) {
    var buttons;
    switch (taskType) {
        case 'info':
            buttons = ['btn-info'];
            break;
        case 'install':
            buttons = ['btn-install', 'btn-refresh-apps'];
            break;
        case 'tweaks':
            buttons = ['btn-tweaks-start'];
            break;
        case 'wifi':
            buttons = ['btn-wifi-read', 'btn-wifi-write'];
            break;
        case 'firmware':
            buttons = ['btn-firmware'];
            break;
        case 'backup':
            buttons = ['btn-backup-download', 'btn-backup-restore'];
            break;
        case 'system':
            buttons = ['btn-diagnose'];
            break;
        default:
            buttons = [];
    }
    for (var i = 0; i < buttons.length; i++) {
        var el = document.getElementById(buttons[i]);
        if (el) el.disabled = disabled;
    }
}

var selectedApk = false;

function updateInstallButton() {
    var select = document.getElementById('app-select');
    var btn = document.getElementById('btn-install');
    var appSelected = select.value;
    if (appSelected) {
        selectedApk = false;
        document.getElementById('apk-filename').textContent = 'No file selected';
        btn.textContent = 'Install ' + select.options[select.selectedIndex].text;
        btn.disabled = false;
    } else if (selectedApk) {
        var name = document.getElementById('apk-filename').textContent;
        btn.textContent = 'Install ' + name.replace(/\.apk$/i, '');
        btn.disabled = false;
    } else {
        btn.textContent = 'Select App to Install';
        btn.disabled = true;
    }
}

function installSelected() {
    var select = document.getElementById('app-select');
    var pkg = select.value;
    if (pkg) {
        api.installApp(pkg);
    } else if (selectedApk) {
        api.installApk();
    }
}

var KEY_TYPES = ['None', 'WEP', 'WPA/WPA2'];

function readWifi() {
    var multi = document.getElementById('wifi-multi').checked;
    api.readWifi(multi);
}

function writeWifi() {
    var entries = document.querySelectorAll('.wifi-entry');
    var networks = [];
    for (var i = 0; i < entries.length; i++) {
        var sid = entries[i].querySelector('.wifi-ssid').value;
        var key = entries[i].querySelector('.wifi-key').value;
        var keyType = parseInt(entries[i].querySelector('.wifi-keytype').value, 10);
        if (sid) {
            networks.push({sid: sid, key: key, keyType: keyType});
        }
    }
    var multi = document.getElementById('wifi-multi').checked;
    api.writeWifi(networks, multi);
}

function renderWifiNetworks(networks) {
    var list = document.getElementById('wifi-list');
    list.innerHTML = '';
    if (!networks || networks.length === 0) {
        list.innerHTML = '<div style="font-size:13px;color:var(--color-text-secondary);">No WiFi networks stored on camera</div>';
    } else {
        for (var i = 0; i < networks.length; i++) {
            list.appendChild(createWifiEntry(networks[i]));
        }
    }
    document.getElementById('wifi-actions').style.display = 'block';
}

function createWifiEntry(net) {
    var div = document.createElement('div');
    div.className = 'wifi-entry';
    var keyTypeOptions = '';
    for (var i = 0; i < KEY_TYPES.length; i++) {
        keyTypeOptions += '<option value="' + i + '"' + (net && net.keyType === i ? ' selected' : '') + '>' + KEY_TYPES[i] + '</option>';
    }
    div.innerHTML =
        '<div class="input-row">' +
            '<label>SSID</label>' +
            '<input type="text" class="wifi-ssid" value="' + escapeAttr(net ? net.sid : '') + '" placeholder="Network name">' +
            '<button class="wifi-remove" onclick="this.closest(\'.wifi-entry\').remove()" title="Remove">' +
                '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
            '</button>' +
        '</div>' +
        '<div class="input-row">' +
            '<label>Key</label>' +
            '<input type="text" class="wifi-key" value="' + escapeAttr(net ? net.key : '') + '" placeholder="Password">' +
        '</div>' +
        '<div class="input-row">' +
            '<label>Type</label>' +
            '<select class="wifi-keytype">' + keyTypeOptions + '</select>' +
        '</div>';
    return div;
}

function addWifiNetwork() {
    var list = document.getElementById('wifi-list');
    var placeholder = list.querySelector('div[style]');
    if (placeholder && !list.querySelector('.wifi-entry')) {
        list.innerHTML = '';
    }
    list.appendChild(createWifiEntry(null));
}

function escapeAttr(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function startTweaks() {
    var mode = document.getElementById('tweaks-mode-select').value;
    if (mode === 'updater') {
        api.startTweaksUpdater();
    } else {
        api.startTweaksService();
    }
}

var tweaksPlaceholderHtml = null;

function showTweaksModal(tweaks) {
    var list = document.getElementById('tweaks-list');
    if (!tweaksPlaceholderHtml) tweaksPlaceholderHtml = list.innerHTML;
    list.innerHTML = '';
    for (var i = 0; i < tweaks.length; i++) {
        var t = tweaks[i];
        var item = document.createElement('label');
        item.className = 'tweak-item';
        item.innerHTML =
            '<input type="checkbox"' + (t.enabled ? ' checked' : '') + ' data-id="' + t.id + '">' +
            '<div class="tweak-info"><div class="tweak-desc">' + escapeHtml(t.desc) + '</div>' +
            '<div class="tweak-value">' + escapeHtml(t.value) + '</div></div>';
        item.querySelector('input').addEventListener('change', (function(id) {
            return function(e) { api.setTweak(id, e.target.checked); };
        })(t.id));
        list.appendChild(item);
    }
    document.getElementById('btn-apply-tweaks').disabled = false;
    document.getElementById('btn-cancel-tweaks').disabled = false;
}

function hideTweaksModal() {
    document.getElementById('btn-apply-tweaks').disabled = true;
    document.getElementById('btn-cancel-tweaks').disabled = true;
    if (tweaksPlaceholderHtml) {
        document.getElementById('tweaks-list').innerHTML = tweaksPlaceholderHtml;
    }
}

function setTweaksModalDisabled(disabled) {
    var panel = document.getElementById('tweaks-inline-panel');
    var inputs = panel.querySelectorAll('input, button');
    for (var i = 0; i < inputs.length; i++) {
        inputs[i].disabled = disabled;
    }
}

function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}


function downloadBackup() {
    var mode = document.getElementById('backup-mode-select').value;
    var parsedtextproperties = document.getElementById('backup-parsedtextproperties').checked;
    api.downloadBackup(mode, parsedtextproperties);
}

function restoreBackup() {
    showConfirmModal('Restore Backup', 'This will overwrite ALL camera settings. This action cannot be undone.', function() {
        var mode = document.getElementById('backup-mode-select').value;
        api.restoreBackup(mode);
    });
}

var _confirmModalCallback = null;

function showConfirmModal(title, message, onConfirm) {
    document.getElementById('confirm-modal-title').textContent = title;
    document.getElementById('confirm-modal-message').textContent = message;
    document.getElementById('confirm-modal').style.display = 'flex';
    _confirmModalCallback = onConfirm;
}

function closeConfirmModal(confirmed) {
    document.getElementById('confirm-modal').style.display = 'none';
    if (confirmed && _confirmModalCallback) {
        _confirmModalCallback();
    }
    _confirmModalCallback = null;
}

function renderDiagnostics(checks) {
    var container = document.getElementById('diagnostics-results');
    var list = document.getElementById('diagnostics-list');
    container.style.display = 'block';
    list.innerHTML = '';
    if (!checks || !checks.length) {
        list.innerHTML = '<div style="font-size:13px;color:var(--color-text-secondary);">No diagnostics to report.</div>';
        return;
    }
    for (var i = 0; i < checks.length; i++) {
        var c = checks[i];
        var statusClass = c.status === 'pass' ? 'diag-pass' : (c.status === 'warn' ? 'diag-warn' : 'diag-fail');
        var icon = c.status === 'pass' ? '✓' : (c.status === 'warn' ? '⚠' : '✗');
        var div = document.createElement('div');
        div.className = 'diag-item ' + statusClass;
        var html = '<div class="diag-header"><span class="diag-icon">' + icon + '</span><span class="diag-label">' + escapeHtml(c.label) + '</span></div>';
        if (c.detail) {
            html += '<div class="diag-detail">' + escapeHtml(c.detail) + '</div>';
        }
        if (c.solution && c.status !== 'pass') {
            html += '<div class="diag-solution">' + escapeHtml(c.solution) + '</div>';
        }
        div.innerHTML = html;
        list.appendChild(div);
    }
}

// Tab switching
document.getElementById('tabs').addEventListener('click', function(e) {
    var tab = e.target.closest('.tab');
    if (!tab) return;
    var tabId = tab.dataset.tab;

    var allTabs = document.querySelectorAll('.tab');
    var allPanels = document.querySelectorAll('.tab-panel');
    for (var i = 0; i < allTabs.length; i++) allTabs[i].classList.remove('active');
    for (var i = 0; i < allPanels.length; i++) allPanels[i].classList.remove('active');

    tab.classList.add('active');
    document.getElementById('panel-' + tabId).classList.add('active');
});

// Copy camera info value on click
document.getElementById('camera-info-display').addEventListener('click', function(e) {
    var cell = e.target.closest('.camera-info-value.copyable');
    if (!cell) return;
    var text = cell.textContent;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() { showToast('Copied: ' + text); }).catch(function() { copyFallback(text); showToast('Copied: ' + text); });
    } else {
        copyFallback(text);
        showToast('Copied: ' + text);
    }
});

// Hide optional rows initially
(function() {
    var optionalFields = ['Lens', 'GPS Data'];
    var cells = document.querySelectorAll('#camera-info-display .camera-info-value');
    for (var i = 0; i < cells.length; i++) {
        var field = cells[i].getAttribute('data-field');
        if (optionalFields.indexOf(field) !== -1) {
            cells[i].closest('tr').style.display = 'none';
        }
    }
})();

// Init
window.addEventListener('pywebviewready', function() {
    pywebview.api.get_config().then(function(cfg) {
        if (cfg.version) {
            document.getElementById('app-title').textContent += ' ' + cfg.version;
        }
        document.getElementById('docs-link').href = cfg.docsUrl + '/devices.html';
        document.getElementById('app-source-link').href =
            'https://github.com/' + cfg.githubAppListUser + '/' + cfg.githubAppListRepo;
    });
    pywebview.api.load_apps();
});
