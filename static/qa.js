'use strict';
const $ = id => document.getElementById(id);
let environments = {}, compareTime = '', historyPage = 1, historyController, smokeController;
let smokeLoaded = false, smokeRunning = false;
function node(tag,text,className) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
}
async function jsonResponse(response) {
    let data;
    try { data = await response.json(); } catch { throw new Error('Серверээс мэдээлэл авч чадсангүй.'); }
    if (!response.ok) throw new Error(data.error || 'Хүсэлт амжилтгүй.');
    return data;
}
const timeText = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('mn-MN') : 'Бүртгээгүй';
const imageKey = deployment => JSON.stringify(Object.entries(deployment.images).sort(([a],[b]) => a.localeCompare(b)));
function differences(a,b,unavailable) {
    if (unavailable) return ['Харьцуулах мэдээлэл дутуу'];
    if (!a || !b) return ['Нэг орчинд байхгүй'];
    const result = [];
    if (imageKey(a) !== imageKey(b)) result.push('Image');
    if (a.build !== b.build) result.push('Build');
    if (a.replicas !== b.replicas) result.push('Replica');
    if (['ready','updated','available','observed','paused'].some(k => a[k] !== b[k])) result.push('Төлөв');
    return result;
}
function environmentCell(deployment,error) {
    const cell = node('td',undefined,'wrap-text');
    if (error || !deployment) { cell.textContent = error ? 'Мэдээлэл боломжгүй' : 'Deployment алга'; return cell; }
    Object.entries(deployment.images).forEach(([name,image]) => cell.append(node('div',`${name}: ${image}`,'version-image')));
    cell.append(node('small',`Build: ${deployment.build || 'Бүртгээгүй'} · Revision: ${deployment.revision || '—'}`,'image-label'),
        node('small',`Deploy: ${timeText(deployment.deployed_at)}`,'image-label'),
        node('small',`${deployment.ready}/${deployment.replicas} Ready · ${deployment.updated} Updated · ${deployment.available} Available${deployment.paused ? ' · Paused' : ''}${!deployment.observed ? ' · Шинэчлэлт хүлээж байна' : ''}`,'image-label'));
    return cell;
}
function renderComparison() {
    const test = environments['test-ms'] || {items:[],error:'Мэдээлэл алга'}, pre = environments['pre-new-ms'] || {items:[],error:'Мэдээлэл алга'};
    const left = new Map(test.items.map(d => [d.name,d])), right = new Map(pre.items.map(d => [d.name,d]));
    const names = [...new Set([...left.keys(),...right.keys()])].sort();
    const rows = [];
    names.forEach(name => {
        const a = left.get(name), b = right.get(name), diff = differences(a,b,test.error || pre.error);
        if (!name.toLowerCase().includes($('compareQuery').value.trim().toLowerCase()) || $('differencesOnly').checked && !diff.length) return;
        const row = node('tr'); row.append(node('td',name,'pod-name'),environmentCell(a,test.error),environmentCell(b,pre.error),
            node('td',diff.length ? diff.join(', ') : 'Ижил',diff.length ? 'restart-warning' : '')); rows.push(row);
    });
    $('compareRows').replaceChildren(...rows);
    const errors = Object.entries(environments).filter(([,env]) => env.error).map(([ns,env]) => `${ns}: ${env.error}`);
    $('compareStatus').textContent = [...errors,`${rows.length} deployment · ${timeText(compareTime)}`].join(' · ');
}
async function loadComparison() {
    $('refreshCompare').disabled = true; $('compareStatus').textContent = 'Хоёр орчны мэдээлэл татаж байна…';
    try { const data = await jsonResponse(await fetch('/api/deployments/compare')); environments = data.environments; compareTime = data.captured_at; renderComparison(); }
    catch (error) { environments = {}; $('compareRows').replaceChildren(); $('compareStatus').textContent = error.message; }
    finally { $('refreshCompare').disabled = false; }
}
async function loadSmoke() {
    smokeController?.abort(); const controller = new AbortController(); smokeController = controller;
    smokeLoaded = false; $('runSmoke').disabled = true; $('smokeChecks').replaceChildren(); $('smokeRows').replaceChildren();
    $('smokeStatus').textContent = 'Шалгалтуудыг татаж байна…';
    try {
        const data = await jsonResponse(await fetch(`/api/smoke/${encodeURIComponent($('smokeNamespace').value)}`,{signal:controller.signal}));
        if (smokeController !== controller) return;
        $('smokeChecks').replaceChildren(...data.checks.map(check => {
            const label = node('label',undefined,'checkbox'), input = node('input'); input.type = 'checkbox'; input.value = check.id; input.checked = true;
            label.append(input,node('span',`${check.name} · ${check.kind}${check.expected_status ? ' → ' + check.expected_status : ''}`)); return label;
        }));
        smokeLoaded = true; $('runSmoke').disabled = false;
        $('smokeStatus').textContent = data.checks.length === 1 ? 'API URL тохируулаагүй. Deployment readiness шалгалт бэлэн.' : `${data.checks.length} шалгалт бэлэн.`;
    } catch (error) { if (error.name !== 'AbortError') $('smokeStatus').textContent = error.message; }
}
async function runSmoke() {
    if (smokeRunning || !smokeLoaded) return;
    const ids = [...$('smokeChecks').querySelectorAll('input:checked')].map(el => el.value);
    if (!ids.length) { $('smokeStatus').textContent = 'Шалгалтаа сонгоно уу.'; return; }
    smokeRunning = true; $('runSmoke').disabled = true; $('smokeNamespace').disabled = true;
    $('smokeRows').replaceChildren(); $('smokeStatus').textContent = 'Шалгаж байна…';
    try {
        const data = await jsonResponse(await fetch(`/api/smoke/${encodeURIComponent($('smokeNamespace').value)}`,{
            method:'POST',headers:{'Content-Type':'application/json','X-QA-Request':'smoke'},body:JSON.stringify({ids})}));
        $('smokeRows').replaceChildren(...data.results.map(result => {
            const row = node('tr'); [result.name,result.ok ? 'PASS' : 'FAIL',result.status ?? '—',
                result.duration_ms === undefined ? '—' : `${result.duration_ms} ms`,
                [result.message,...(result.blocked || []).map(b => `${b.deployment}: ${b.restart_reason}`)].join('\n')]
                .forEach(value => row.append(node('td',value,'wrap-text'))); return row;
        }));
        $('smokeStatus').textContent = [data.ok ? 'Бүх шалгалт амжилттай.' : 'Зарим шалгалт амжилтгүй.',timeText(data.completed_at),data.audit_warning].filter(Boolean).join(' ');
    } catch (error) { $('smokeStatus').textContent = error.message; }
    finally { smokeRunning = false; $('runSmoke').disabled = false; $('smokeNamespace').disabled = false; }
}
function historyDetails(item) {
    if (item.action === 'allowlist') return Object.entries(item.details.entries || {}).map(([kind,status]) => `${kind}: ${status === 'added' ? 'Нэмсэн' : 'Бүртгэлтэй'}`).join(', ') || 'Бүртгэх үйлдэл амжилтгүй';
    if (item.action === 'smoke') return (item.details.checks || []).map(c => `${c.id}: ${c.ok ? 'PASS' : 'FAIL'}`).join(', ');
    return `HTTP ${item.http_status}`;
}
async function loadHistory() {
    historyController?.abort(); const controller = new AbortController(); historyController = controller;
    $('refreshHistory').disabled = true; $('historyPrev').disabled = true; $('historyNext').disabled = true;
    $('historyStatus').textContent = 'Түүх татаж байна…';
    const params = new URLSearchParams({namespace:$('historyNamespace').value,action:$('historyAction').value,page:historyPage});
    try {
        const data = await jsonResponse(await fetch(`/api/history?${params}`,{signal:controller.signal}));
        if (historyController !== controller) return;
        $('historyRows').replaceChildren(...data.items.map(item => {
            const row = node('tr'); [timeText(item.created_at),item.namespace,{rollout_restart:'Rollout restart',allowlist:'Email / SMS',smoke:'Smoke test'}[item.action],
                item.target || '—',item.http_status === 409 ? 'Хаагдсан' : item.outcome === 'success' ? 'Амжилттай' : 'Амжилтгүй',historyDetails(item)]
                .forEach(value => row.append(node('td',value,'wrap-text'))); return row;
        }));
        $('historyPage').textContent = data.page; $('historyStatus').textContent = data.items.length ? `${data.items.length} үйлдэл` : 'Үйлдлийн түүх алга.';
        $('historyPrev').disabled = historyPage <= 1; $('historyNext').disabled = !data.has_more;
    } catch (error) { if (error.name !== 'AbortError') { $('historyRows').replaceChildren(); $('historyStatus').textContent = error.message; } }
    finally { if (historyController === controller) $('refreshHistory').disabled = false; }
}
document.querySelectorAll('[data-panel]').forEach(button => button.addEventListener('click',() => {
    document.querySelectorAll('[data-panel]').forEach(tab => {
        const active = tab === button; tab.classList.toggle('primary',active); tab.setAttribute('aria-pressed',String(active)); $(tab.dataset.panel).hidden = !active;
    });
    if (button.dataset.panel === 'smokePanel' && !smokeLoaded && !smokeRunning) loadSmoke();
    if (button.dataset.panel === 'historyPanel') loadHistory();
}));
$('refreshCompare').addEventListener('click',loadComparison);
$('compareQuery').addEventListener('input',renderComparison); $('differencesOnly').addEventListener('change',renderComparison);
$('smokeNamespace').addEventListener('change',loadSmoke); $('runSmoke').addEventListener('click',runSmoke);
$('refreshHistory').addEventListener('click',loadHistory);
['historyNamespace','historyAction'].forEach(id => $(id).addEventListener('change',() => {historyPage = 1; loadHistory();}));
$('historyPrev').addEventListener('click',() => {if (historyPage > 1) {historyPage--; loadHistory();}});
$('historyNext').addEventListener('click',() => {historyPage++; loadHistory();});
loadComparison();
