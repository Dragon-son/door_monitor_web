// ========== Tab 管理(顶部 tabbar) ==========
//
// 模型:
//   openTabs: 顺序数组,每项 {key, label, icon, closable}
//   activeTabKey: 当前激活 tab 的 key
//
// 公开 API(挂在 window):
//   openTab(key)        — 打开或激活某个模块 tab(同一模块只一个)
//   closeTab(key)       — 关闭某个 tab(主页不能关)
//   onTabClose(key, cb) — 注册关闭钩子(供 monitor/playback 注册资源清理)

(function () {
    const TAB_META = {
        home:     { label: '主页',     icon: '🏠', closable: false },
        access:   { label: '门禁控制', icon: '🚪', closable: true, permission: 'page.access' },
        monitor:  { label: '视频监控', icon: '🎥', closable: true, permission: 'page.monitor' },
        playback: { label: '视频回放', icon: '🎬', closable: true, permission: 'page.playback' },
        devices:  { label: '设备管理', icon: '📡', closable: true, permission: 'page.devices' },
        persons:  { label: '人员管理', icon: '👥', closable: true, permission: 'page.persons' },
        admin:    { label: '用户管理', icon: '🛡️', closable: true, permission: 'page.admin'    },
        audit:    { label: '操作日志', icon: '📋', closable: true, permission: 'page.audit'    },
    };

    let openTabs = [];
    let activeTabKey = 'home';
    const closeHooks = {}; // key -> [callback,...]

    function hasRequiredDeviceType(type) {
        if (!type) return true;
        // 注意：globals.js 中的 `let devices` 是全局 let 绑定，不会挂在 window 上，
        // 因此用 typeof 守卫直接读裸 `devices`，而不是 window.devices。
        if (typeof devices === 'undefined' || !Array.isArray(devices)) return false;
        if (type === 'nvr')    return devices.some(d => d && d.device_type === 'nvr');
        if (type === 'access') return devices.some(d => d && (d.device_type || 'access') === 'access');
        return true;
    }

    function _findIndex(key) {
        return openTabs.findIndex(t => t.key === key);
    }

    function openTab(key) {
        const meta = TAB_META[key];
        if (!meta) {
            console.warn('[tabs] 未知 tab key:', key);
            return;
        }
        if (meta.permission && typeof hasPermission === 'function' && !hasPermission(meta.permission)) {
            const msg = `当前账户没有「${meta.label}」权限`;
            if (typeof toast === 'function') toast('warn', msg);
            else console.warn('[tabs]', msg);
            return;
        }
        if (meta.requireDeviceType && !hasRequiredDeviceType(meta.requireDeviceType)) {
            const msg = meta.requireDeviceType === 'nvr'
                ? '当前账户没有可用的录像机设备'
                : '当前账户没有可用的门禁设备';
            if (typeof toast === 'function') toast('warn', msg);
            else console.warn('[tabs]', msg);
            return;
        }
        const idx = _findIndex(key);
        if (idx < 0 && key !== 'home') {
            openTabs.push({ key, ...meta });
        }
        activeTabKey = key;
        _applyActivePage(key);
        renderTabbar();
        // 调原 showPage 的页面 init 钩子,通过别名 _showPageInternal
        if (typeof _showPageInternal === 'function') _showPageInternal(key);
    }

    function closeTab(key) {
        const idx = _findIndex(key);
        if (idx < 0) return;
        if (!openTabs[idx].closable) return;
        // 触发关闭钩子
        (closeHooks[key] || []).forEach(cb => {
            try { cb(); } catch (e) { console.warn('[tabs] onTabClose hook 出错', e); }
        });
        const wasActive = (key === activeTabKey);
        openTabs.splice(idx, 1);
        if (wasActive) {
            // 切回最后一个剩余 tab(或主页)
            const next = openTabs[openTabs.length - 1] || { key: 'home' };
            openTab(next.key);
            return; // openTab 内部会 renderTabbar
        }
        renderTabbar();
    }

    function activateTab(key) {
        // 仅切换 active(假设已经打开)
        if (_findIndex(key) < 0) {
            openTab(key);
            return;
        }
        activeTabKey = key;
        _applyActivePage(key);
        renderTabbar();
        if (typeof _showPageInternal === 'function') _showPageInternal(key);
    }

    function onTabClose(key, cb) {
        if (!closeHooks[key]) closeHooks[key] = [];
        closeHooks[key].push(cb);
    }

    function _applyActivePage(key) {
        if (typeof currentPageName !== 'undefined') currentPageName = key;
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        const pageEl = document.getElementById('page-' + key);
        if (pageEl) pageEl.classList.add('active');
    }

    function renderTabbar() {
        const bar = document.getElementById('topbarTabs');
        if (!bar) return;
        let html = '';
        openTabs.forEach(t => {
            const active = t.key === activeTabKey ? ' active' : '';
            const closeBtn = t.closable
                ? `<span class="tab-close" onclick="event.stopPropagation();closeTab('${t.key}')" title="关闭">✕</span>`
                : '';
            html += `<div class="topbar-tab${active}" onclick="activateTab('${t.key}')" data-tab="${t.key}">
                <span class="tab-icon">${t.icon}</span><span>${t.label}</span>${closeBtn}
            </div>`;
        });
        bar.innerHTML = html;
    }

    // 暴露
    window.openTab = openTab;
    window.closeTab = closeTab;
    window.activateTab = activateTab;
    window.onTabClose = onTabClose;
    window.renderTabbar = renderTabbar;
    window.hasRequiredDeviceType = hasRequiredDeviceType;
    // 关闭那些依赖设备类型但当前用户已无对应设备的已打开 tab。
    window.closeOrphanTabs = function () {
        openTabs.slice().forEach(t => {
            if (t.requireDeviceType && !hasRequiredDeviceType(t.requireDeviceType)) {
                closeTab(t.key);
            }
        });
    };
    window.__getActiveTabKey = () => activeTabKey;
    window.__getOpenTabs = () => openTabs.slice();
})();
