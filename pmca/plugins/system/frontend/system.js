/*
 * System diagnostics plugin - web GUI frontend.
 *
 * Injected into the page by app.js only when the system backend is present.
 * Self-registers a "System" tab through window.PMCA and talks to the backend
 * via api.pluginCall('system', 'run').
 */
(function () {
    const P = window.PMCA;
    if (!P || typeof P.registerTab !== 'function') return;

    const { html, useState, useApp, useBus, Card, Icon, api, copyText } = P;

    const DIAG = {
        pass: { cls: 'diag-pass', icon: '✓', text: 'PASS' },
        warn: { cls: 'diag-warn', icon: '⚠', text: 'WARN' },
        fail: { cls: 'diag-fail', icon: '✗', text: 'FAIL' },
    };

    function diagnosticsToText(checks) {
        const counts = { pass: 0, warn: 0, fail: 0 };
        const lines = ['System Diagnostics', '=================='];
        checks.forEach((c) => {
            if (counts[c.status] !== undefined) counts[c.status]++;
            const tag = (DIAG[c.status] || {}).text || c.status.toUpperCase();
            lines.push('');
            lines.push('[' + tag + '] ' + c.label);
            if (c.detail) lines.push('  ' + c.detail);
            if (c.solution && c.status !== 'pass') lines.push('  Solution: ' + c.solution);
        });
        lines.push('');
        lines.push('Summary: ' + counts.pass + ' passed, ' + counts.warn + ' warnings, ' + counts.fail + ' failures');
        return lines.join('\n');
    }

    function SystemTab() {
        const { busy, showToast } = useApp();
        const [checks, setChecks] = useState(null);
        useBus('diagnostics_result', (data) => setChecks(data || []));

        const copyResults = () =>
            copyText(diagnosticsToText(checks)).then(() => showToast('Copied to clipboard'));

        return html`
            <${Card}>
                <button class="btn-primary btn-block" disabled=${busy.system}
                        onClick=${() => api.pluginCall('system', 'run')}>
                    Diagnose host system
                </button>
            <//>
            ${checks !== null && html`
                <div class="card">
                    <div class="card-title-row">
                        <div class="card-title">Diagnostics</div>
                        ${checks.length > 0 && html`
                            <button class="ghost-icon-btn" title="Copy results" onClick=${copyResults}>
                                <${Icon} path=${html`<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>`}/>
                            </button>`}
                    </div>
                    ${checks.length === 0
                        ? html`<div class="muted">No diagnostics to report.</div>`
                        : checks.map((c, i) => {
                            const d = DIAG[c.status] || DIAG.fail;
                            return html`
                                <div class="diag-item ${d.cls}" key=${i}>
                                    <div class="diag-header">
                                        <span class="diag-icon">${d.icon}</span>
                                        <span>${c.label}</span>
                                    </div>
                                    ${c.detail && html`<div class="diag-detail">${c.detail}</div>`}
                                    ${c.solution && c.status !== 'pass' && html`<div class="diag-solution">${c.solution}</div>`}
                                </div>`;
                        })}
                </div>`}`;
    }

    const icon = html`<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>`;

    P.registerTab({ id: 'system', label: 'System', component: SystemTab, icon: icon, order: 50 });
})();
