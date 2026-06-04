// ========== 主页卡片 ==========

const HOME_CARDS = [
    { key: 'access',   icon: '🚪', title: '门禁控制', desc: '远程开门 / 门状态 / 通行日志', permission: 'page.access' },
    { key: 'monitor',  icon: '🎥', title: '视频监控', desc: '录像机实时画面',               permission: 'page.monitor' },
    { key: 'playback', icon: '🎬', title: '视频回放', desc: '历史录像查询和回放',           permission: 'page.playback' },
    { key: 'devices',  icon: '📡', title: '设备管理', desc: '门禁/录像机配置',             permission: 'page.devices' },
    { key: 'persons',  icon: '👥', title: '人员管理', desc: '人员名单 / 人脸 / 权限',       permission: 'page.persons' },
    { key: 'admin',    icon: '🛡️', title: '用户管理', desc: '系统用户和权限',              permission: 'page.admin'    },
    { key: 'audit',    icon: '📋', title: '操作日志', desc: '操作审计追溯',                permission: 'page.audit'    },
];

function renderHomeCards() {
    const grid = document.getElementById('homeGrid');
    if (!grid) return;

    // 设备列表未加载完成前先不过滤设备相关卡片，避免显示空白。
    const devicesReady = (typeof _devicesLoaded !== 'undefined' && _devicesLoaded);
    let html = '';
    HOME_CARDS.forEach(c => {
        if (c.permission && typeof hasPermission === 'function' && !hasPermission(c.permission)) return;
        if (devicesReady && c.requireDeviceType
            && typeof hasRequiredDeviceType === 'function'
            && !hasRequiredDeviceType(c.requireDeviceType)) return;
        html += `<div class="home-card" onclick="openTab('${c.key}')">
            <div class="home-card-icon">${c.icon}</div>
            <div class="home-card-text">
                <div class="home-card-title">${escapeHtml(c.title)}</div>
                <div class="home-card-desc">${escapeHtml(c.desc)}</div>
            </div>
        </div>`;
    });
    grid.innerHTML = html;
}
