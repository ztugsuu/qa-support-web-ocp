'use strict';
const $ = id => document.getElementById(id);
let pods = [], loaded = false, listController, statsController, statsTimer, selectedPod;
let listFresh = false;
const pendingRestarts = new Set();
const attention = pod => !['Running', 'Succeeded'].includes(pod.status);
const restarts = pod => String(pod.restarts).split(',').reduce((sum, value) => sum + (Number(value) || 0), 0);
const podPath = (ns, name) => `${encodeURIComponent(ns)}/${encodeURIComponent(name)}`;
function age(value) {
    const time = Date.parse(value);
    if (!Number.isFinite(time)) return value || '—';
    const minutes = Math.max(0, Math.floor((Date.now() - time) / 60000));
    if (minutes < 1) return '< 1 мин';
    if (minutes < 60) return `${minutes} мин`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)} цаг`;
    return `${Math.floor(minutes / 1440)} өдөр`;
}
function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}
function render() {
    const query = $('searchInput').value.trim().toLowerCase(), status = $('statusFilter').value;
    const filtered = pods.filter(p => p.name.toLowerCase().includes(query) && (status === 'all' || (status === 'attention' ? attention(p) : p.status === status)));
    filtered.sort((a, b) => $('sortSelect').value === 'restarts' ? restarts(b) - restarts(a) || a.name.localeCompare(b.name) : $('sortSelect').value === 'newest' ? (Date.parse(b.age) || 0) - (Date.parse(a.age) || 0) : a.name.localeCompare(b.name));
    const fragment = document.createDocumentFragment();
    filtered.forEach(pod => {
        const row = element('tr');
        const nameCell = element('td', 'pod-name', pod.name);
        Object.entries(pod.images || {}).forEach(([name, img]) => nameCell.append(element('small', 'image-label', `${name}: ${img}`)));
        if (pod.version?.build) nameCell.append(element('small', 'image-label', `Build: ${pod.version.build}`));
        row.append(nameCell);
        const statusCell = element('td');
        statusCell.append(element('span', `badge ${pod.status === 'Running' ? 'running' : pod.status === 'Pending' ? 'pending' : attention(pod) ? 'failed' : ''}`, pod.status));
        row.append(statusCell, element('td', restarts(pod) > 0 ? 'restart-warning' : '', String(restarts(pod))));
        const ageCell = element('td', 'age', age(pod.age));
        ageCell.title = Number.isFinite(Date.parse(pod.age)) ? new Date(pod.age).toLocaleString('mn-MN') : pod.age;
        row.append(ageCell);
        const actions = element('td'), group = element('div', 'row-actions'), resources = element('button', 'button resource-button', 'Нөөц'), logs = element('a', 'button', 'Лог ↗');
        resources.addEventListener('click', () => openResources(pod.name));
        resources.setAttribute('aria-label', `${pod.name}: нөөц харах`);
        logs.href = `/logs/${podPath($('namespaceSelect').value, pod.name)}`;
        logs.target = '_blank'; logs.rel = 'noopener'; logs.setAttribute('aria-label', `${pod.name}: лог шинэ цонхонд харах`);
        const restart = element('button', 'button restart-button', 'Rollout restart');
        const key = `${$('namespaceSelect').value}/${pod.deployment}`;
        restart.disabled = !listFresh || !pod.can_restart || pendingRestarts.has(key);
        restart.title = pendingRestarts.has(key) ? 'Rollout restart хүсэлт явж байна…' : !listFresh ? 'Төлөвийг шинэчилж шалгах шаардлагатай.' : pod.restart_reason;
        restart.setAttribute('aria-label', `${pod.deployment || pod.name}: rollout restart`);
        restart.addEventListener('click', () => restartDeployment(pod.deployment));
        logs.textContent = 'Лог / Дэлгэрэнгүй ↗';
        group.append(resources, logs, restart); actions.append(group);
        if (restart.disabled) actions.append(element('small', 'restart-reason', restart.title));
        row.append(actions); fragment.append(row);
    });
    $('podsTableBody').replaceChildren(fragment);
    $('visibleCount').textContent = loaded ? filtered.length : '—';
    $('emptyState').hidden = filtered.length > 0;
    if (loaded) {
        $('emptyTitle').textContent = pods.length ? 'Хайлтад тохирох pod олдсонгүй' : 'Энэ namespace-д pod алга';
        $('emptyDescription').textContent = pods.length ? 'Хайх үг эсвэл төлөвийн шүүлтүүрээ өөрчилнө үү.' : 'Өөр namespace сонгох эсвэл жагсаалтыг шинэчилнэ үү.';
        $('resultText').textContent = `${pods.length} pod-оос ${filtered.length} харагдаж байна`;
    }
    $('clearFilters').hidden = !loaded || !(query || status !== 'all');
}
async function loadPods(reset = false) {
    listController?.abort(); const controller = new AbortController(); listController = controller;
    listFresh = false; render();
    if (reset) {
        pods = []; loaded = false; render();
        ['totalCount', 'runningCount', 'attentionCount', 'restartCount'].forEach(id => $(id).textContent = '—');
        $('lastUpdated').textContent = 'Шинэчлэгдээгүй'; $('resultText').textContent = 'Мэдээлэл татаж байна…';
    }
    $('refreshBtn').disabled = true; $('refreshBtn').textContent = '↻ Ачаалж байна…'; $('errorBanner').hidden = true;
    $('podsTableBody').setAttribute('aria-busy', 'true');
    if (!loaded) { $('emptyTitle').textContent = 'Pod жагсаалтыг ачаалж байна…'; $('emptyDescription').textContent = 'Түр хүлээнэ үү.'; }
    try {
        const response = await fetch(`/api/pods?namespace=${encodeURIComponent($('namespaceSelect').value)}`, { signal: controller.signal });
        if (!response.ok) throw new Error('Failed to load pods');
        const data = await response.json();
        if (!Array.isArray(data)) throw new Error('Invalid response');
        if (listController !== controller) return;
        pods = data; loaded = true; listFresh = true;
        $('totalCount').textContent = pods.length;
        $('runningCount').textContent = pods.filter(p => p.status === 'Running').length;
        $('attentionCount').textContent = pods.filter(attention).length;
        $('restartCount').textContent = pods.reduce((sum, p) => sum + restarts(p), 0);
        $('lastUpdated').textContent = `Шинэчилсэн: ${new Date().toLocaleTimeString('mn-MN')}`;
        render();
    } catch (error) {
        if (error.name === 'AbortError') return;
        $('errorBanner').hidden = false;
        $('errorBanner').textContent = loaded ? 'Шинэчлэхэд алдаа гарлаа. Өмнөх мэдээллийг харуулж байна. Дахин оролдоно уу.' : 'Мэдээлэл татаж чадсангүй. Холболтоо шалгаад дахин шинэчилнэ үү.';
        if (!loaded) { $('emptyTitle').textContent = 'Pod жагсаалт боломжгүй байна'; $('emptyDescription').textContent = 'Шинэчлэх товчоор дахин оролдоно уу.'; $('resultText').textContent = 'Мэдээлэл татахад алдаа гарлаа'; }
    } finally {
        if (listController === controller) { $('refreshBtn').disabled = !$('namespaceSelect').value; $('refreshBtn').textContent = '↻ Шинэчлэх'; $('podsTableBody').setAttribute('aria-busy', 'false'); }
    }
}
async function restartDeployment(name) {
    const namespace = $('namespaceSelect').value, key = `${namespace}/${name}`;
    if (!listFresh || !name || pendingRestarts.has(key) || !pods.some(p => p.deployment === name && p.can_restart)) return;
    pendingRestarts.add(key); render();
    $('restartStatus').textContent = `${name}: rollout restart эхлүүлж байна…`;
    try {
        const response = await fetch(`/api/deployments/${podPath(namespace, name)}/restart`, {
            method: 'POST', headers: {'X-QA-Request': 'rollout-restart'}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Rollout restart амжилтгүй.');
        if ($('namespaceSelect').value === namespace) $('restartStatus').textContent = [data.message, data.audit_warning].filter(Boolean).join(' ');
    } catch (error) {
        if ($('namespaceSelect').value === namespace) $('restartStatus').textContent = error.message;
    } finally {
        // Keep every button for this deployment blocked until fresh state arrives.
        if ($('namespaceSelect').value === namespace) { listFresh = false; await loadPods(); }
        pendingRestarts.delete(key); render();
    }
}
async function fetchStats() {
    if (!selectedPod) return;
    statsController?.abort(); const controller = new AbortController(); statsController = controller;
    $('refreshStatsBtn').disabled = true;
    try {
        const response = await fetch(`/api/pod-stats/${podPath(selectedPod.namespace, selectedPod.name)}`, {signal: controller.signal});
        if (!response.ok) throw new Error('Stats unavailable');
        const data = await response.json();
        [['modalCpu','cpu'], ['modalMem','mem'], ['modalCpuLimit','cpu_limit'], ['modalMemLimit','mem_limit']].forEach(([id,key]) => $(id).textContent = data[key] || 'N/A');
        $('statsStatus').textContent = data.cpu === 'N/A' || data.mem === 'N/A' ? 'Зарим хэмжилт одоогоор боломжгүй байна. 3 сек тутам шинэчилнэ.' : '3 сек тутам автоматаар шинэчилж байна.';
    } catch (error) {
        if (error.name === 'AbortError') return;
        ['modalCpu','modalMem','modalCpuLimit','modalMemLimit'].forEach(id => $(id).textContent = '—');
        $('statsStatus').textContent = 'Нөөцийн мэдээлэл татаж чадсангүй. Дахин оролдож байна…';
    } finally {
        if (statsController === controller) {
            $('refreshStatsBtn').disabled = false;
            clearTimeout(statsTimer);
            if ($('resourceModal').open && selectedPod) statsTimer = setTimeout(fetchStats, 3000);
        }
    }
}
function openResources(name) {
    clearTimeout(statsTimer);
    selectedPod = {namespace: $('namespaceSelect').value, name};
    $('modalPodName').textContent = `${selectedPod.namespace} / ${name}`;
    ['modalCpu','modalMem','modalCpuLimit','modalMemLimit'].forEach(id => $(id).textContent = '—');
    $('statsStatus').textContent = 'Нөөцийн мэдээлэл ачаалж байна…';
    $('modalLogs').href = `/logs/${podPath(selectedPod.namespace, name)}`;
    $('resourceModal').showModal(); fetchStats();
}
$('resourceModal').addEventListener('close', () => { clearTimeout(statsTimer); statsController?.abort(); selectedPod = null; });
$('closeModal').addEventListener('click', () => $('resourceModal').close());
$('refreshStatsBtn').addEventListener('click', () => { clearTimeout(statsTimer); fetchStats(); });
$('refreshBtn').addEventListener('click', () => loadPods());
$('namespaceSelect').addEventListener('change', () => { $('restartStatus').textContent = ''; loadPods(true); });
['searchInput','statusFilter','sortSelect'].forEach(id => $(id).addEventListener(id === 'searchInput' ? 'input' : 'change', render));
$('clearFilters').addEventListener('click', () => { $('searchInput').value = ''; $('statusFilter').value = 'all'; render(); });
document.addEventListener('keydown', event => { if (event.key === '/' && !event.ctrlKey && !event.metaKey && !$('resourceModal').open && !['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)) { event.preventDefault(); $('searchInput').focus(); } });
if ($('namespaceSelect').value) loadPods(true);
else { $('refreshBtn').disabled = true; $('emptyTitle').textContent = 'Namespace тохируулаагүй байна'; $('emptyDescription').textContent = 'Namespace тохиргоог шалгана уу.'; $('resultText').textContent = 'Namespace алга'; }
// Keep eligibility current while the dashboard is visible.
setInterval(() => { if ($('namespaceSelect').value && !document.hidden && !$('refreshBtn').disabled) loadPods(); }, 5000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && $('namespaceSelect').value) loadPods(); });
