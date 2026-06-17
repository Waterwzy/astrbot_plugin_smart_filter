const bridge = window.AstrBotPluginPage;

let violations = [];
let selectedUsers = new Set();

function getUserKey(platform, userId) {
    return `${platform}:${userId}`;
}

function formatTime(timestamp) {
    const date = new Date(timestamp * 1000);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function showMessage(text, type = 'info') {
    const messageEl = document.getElementById('message');
    messageEl.textContent = text;
    messageEl.className = `message message-${type}`;
    setTimeout(() => {
        messageEl.textContent = '';
        messageEl.className = 'message';
    }, 3000);
}

function groupViolationsByUser(violationsList) {
    const grouped = {};
    violationsList.forEach(v => {
        const key = getUserKey(v.platform, v.user_id);
        if (!grouped[key]) {
            grouped[key] = {
                platform: v.platform,
                user_id: v.user_id,
                messages: [],
                is_banned: v.is_banned
            };
        }
        grouped[key].messages.push(v);
    });
    return Object.values(grouped);
}

function renderTable() {
    const tbody = document.getElementById('violationsBody');
    const grouped = groupViolationsByUser(violations);

    if (grouped.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="no-data">暂无违规消息</td></tr>';
        return;
    }

    let html = '';
    grouped.forEach(user => {
        const key = getUserKey(user.platform, user.user_id);
        const isSelected = selectedUsers.has(key);
        const messageCount = user.messages.length;

        user.messages.forEach((msg, index) => {
            const isLast = index === messageCount - 1;
            const isFirst = index === 0;

            html += '<tr>';
            if (isFirst) {
                html += `<td class="checkbox-column" rowspan="${messageCount}">
                    <input type="checkbox" class="user-checkbox" data-key="${key}" ${isSelected ? 'checked' : ''}>
                </td>`;
                html += `<td class="user-column" rowspan="${messageCount}">
                    <div class="user-info">
                        <span class="user-id">${user.user_id}</span>
                        <span class="platform-badge">${user.platform}</span>
                        ${user.is_banned ? '<span class="banned-badge">已封禁</span>' : ''}
                    </div>
                </td>`;
            }
            html += `<td class="message-column">${msg.message}</td>`;
            html += `<td class="time-column">${formatTime(msg.time)}</td>`;
            if (isFirst) {
                html += `<td class="action-column" rowspan="${messageCount}">
                    <button class="btn btn-danger btn-sm clear-btn" data-key="${key}">清除</button>
                </td>`;
                html += `<td class="unban-column" rowspan="${messageCount}">
                    ${user.is_banned ? `<button class="btn btn-success btn-sm unban-btn" data-key="${key}">解封</button>` : ''}
                </td>`;
            }
            html += '</tr>';
        });
    });

    tbody.innerHTML = html;

    document.querySelectorAll('.user-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', (e) => {
            const key = e.target.dataset.key;
            if (e.target.checked) {
                selectedUsers.add(key);
            } else {
                selectedUsers.delete(key);
            }
            updateSelectAll();
        });
    });

    document.querySelectorAll('.clear-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const key = e.target.dataset.key;
            await clearViolations([key]);
        });
    });

    document.querySelectorAll('.unban-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const key = e.target.dataset.key;
            await unbanUser(key);
        });
    });
}

function updateSelectAll() {
    const grouped = groupViolationsByUser(violations);
    const allKeys = grouped.map(u => getUserKey(u.platform, u.user_id));
    const selectAllCheckbox = document.getElementById('selectAll');
    selectAllCheckbox.checked = allKeys.length > 0 && allKeys.every(key => selectedUsers.has(key));
}

async function loadViolations() {
    try {
        violations = await bridge.apiGet('violations/list');
        renderTable();
    } catch (error) {
        showMessage('加载违规消息失败: ' + error.message, 'error');
    }
}

async function banSelectedUsers() {
    if (selectedUsers.size === 0) {
        showMessage('请先选择要封禁的用户', 'warning');
        return;
    }

    const years = parseInt(document.getElementById('years').value) || 0;
    const months = parseInt(document.getElementById('months').value) || 0;
    const days = parseInt(document.getElementById('days').value) || 0;
    const hours = parseInt(document.getElementById('hours').value) || 0;

    if (years === 0 && months === 0 && days === 0 && hours === 0) {
        showMessage('请设置封禁时间', 'warning');
        return;
    }

    const users = [];
    selectedUsers.forEach(key => {
        const [platform, userId] = key.split(':');
        users.push({ platform, user_id: userId });
    });

    try {
        const result = await bridge.apiPost('violations/ban', {
            users,
            duration: { years, months, days, hours }
        });

        const successCount = result.results.filter(r => r.status === 'success').length;
        const failCount = result.results.filter(r => r.status === 'error').length;

        if (failCount === 0) {
            showMessage(`成功封禁 ${successCount} 个用户`, 'success');
        } else {
            showMessage(`封禁完成：成功 ${successCount} 个，失败 ${failCount} 个`, 'warning');
        }

        selectedUsers.clear();
        await loadViolations();
    } catch (error) {
        showMessage('封禁失败: ' + error.message, 'error');
    }
}

async function clearViolations(keys) {
    const users = keys.map(key => {
        const [platform, userId] = key.split(':');
        return { platform, user_id: userId };
    });

    try {
        const result = await bridge.apiPost('violations/clear', { users });
        const successCount = result.results.filter(r => r.status === 'success').length;
        const failCount = result.results.filter(r => r.status === 'error').length;

        if (failCount === 0) {
            showMessage(`成功清除 ${successCount} 个用户的违规记录`, 'success');
        } else {
            showMessage(`清除完成：成功 ${successCount} 个，失败 ${failCount} 个`, 'warning');
        }

        await loadViolations();
    } catch (error) {
        showMessage('清除失败: ' + error.message, 'error');
    }
}

async function unbanUser(key) {
    const [platform, userId] = key.split(':');

    try {
        const result = await bridge.apiPost('violations/unban', {
            users: [{ platform, user_id: userId }]
        });

        const successCount = result.results.filter(r => r.status === 'success').length;
        const failCount = result.results.filter(r => r.status === 'error').length;

        if (failCount === 0) {
            showMessage(`成功解封用户 ${userId}`, 'success');
        } else {
            showMessage(`解封失败：${result.results[0]?.message || '未知错误'}`, 'error');
        }

        await loadViolations();
    } catch (error) {
        showMessage('解封失败: ' + error.message, 'error');
    }
}

async function init() {
    await bridge.ready();

    document.getElementById('selectAll').addEventListener('change', (e) => {
        const grouped = groupViolationsByUser(violations);
        const allKeys = grouped.map(u => getUserKey(u.platform, u.user_id));

        if (e.target.checked) {
            allKeys.forEach(key => selectedUsers.add(key));
        } else {
            selectedUsers.clear();
        }

        document.querySelectorAll('.user-checkbox').forEach(checkbox => {
            checkbox.checked = e.target.checked;
        });
    });

    document.getElementById('banBtn').addEventListener('click', banSelectedUsers);
    document.getElementById('refreshBtn').addEventListener('click', loadViolations);

    await loadViolations();
}

init();