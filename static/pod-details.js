'use strict';
const $ = id => document.getElementById(id);
const namespace = document.body.dataset.namespace, pod = document.body.dataset.pod;
const path = `${encodeURIComponent(namespace)}/${encodeURIComponent(pod)}`;
let logController, displayedLogs = null;
function node(tag, text, className) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
}
const timeText = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('mn-MN') : 'Бүртгээгүй';
function stateText(state) {
    if (state?.running) return `Running · ${timeText(state.running.startedAt)}`;
    if (state?.waiting) return [state.waiting.reason, state.waiting.message].filter(Boolean).join(': ');
    if (state?.terminated) return `${state.terminated.reason || 'Terminated'} · exit ${state.terminated.exitCode} · ${timeText(state.terminated.finishedAt)}`;
    return '—';
}
async function jsonResponse(response) {
    let data;
    try { data = await response.json(); } catch { throw new Error('Серверээс мэдээлэл авч чадсангүй.'); }
    if (!response.ok) throw new Error(data.error || 'Мэдээлэл авахад алдаа гарлаа.');
    return data;
}
function filters() {
    return new URLSearchParams({container:$('logContainer').value, previous:$('logSource').value,
        since:$('logSince').value, tail:$('logTail').value, level:$('logLevel').value, q:$('logQuery').value});
}
function setContainers(names, selected) {
    $('logContainer').replaceChildren(...names.map(name => {
        const option = node('option', name); option.value = name; return option;
    }));
    $('logContainer').value = names.includes(selected) ? selected : names[0] || '';
}
async function loadDetails() {
    $('refreshDetails').disabled = true; $('detailsStatus').textContent = 'Мэдээлэл татаж байна…';
    try {
        const data = await jsonResponse(await fetch(`/api/pod-details/${path}`));
        const d = data.deployment;
        const entries = d ? [['Deployment',d.name], ['Build',d.build || 'Бүртгээгүй'],
            ['Deploy огноо',timeText(d.deployed_at)], ['Сүүлийн rollout restart',timeText(d.restarted_at)],
            ['Replica',`${d.ready}/${d.replicas} Ready · ${d.updated} Updated · ${d.available} Available`],
            ['Revision',d.revision || '—']] : [['Deployment',data.deployment_error || 'Deployment-д харьяалагдахгүй']];
        $('deploymentDetails').replaceChildren(...entries.map(([label,value]) => {
            const group = node('div'); group.append(node('dt',label),node('dd',value)); return group;
        }));
        $('containerRows').replaceChildren(...data.containers.map(c => {
            const row = node('tr'), image = node('td',c.image,'wrap-text');
            image.append(node('small',c.image_id || 'Image ID алга','image-label'));
            row.append(node('td',c.name + (c.init ? ' (init)' : '')),image,
                node('td',`${c.ready ? 'Ready' : 'Not ready'} · ${c.restarts} restart\n${stateText(c.state)}`,'wrap-text'),
                node('td',stateText(c.last_state),'wrap-text')); return row;
        }));
        setContainers(data.containers.map(c => c.name),$('logContainer').value);
        $('eventRows').replaceChildren(...data.events.map(e => {
            const row = node('tr');
            [timeText(e.time),e.type,e.reason,e.message,e.count].forEach(value => row.append(node('td',value,'wrap-text')));
            return row;
        }));
        $('eventsStatus').textContent = data.events_error || (data.events.length ? `${data.events.length} event` : 'Event олдсонгүй.');
        $('detailsStatus').textContent = `${data.pod.status} · ${new Date().toLocaleTimeString('mn-MN')} шинэчилсэн`;
    } catch (error) { $('detailsStatus').textContent = error.message + ' Өмнөх мэдээлэл байвал хуучирсан байж болно.'; }
    finally { $('refreshDetails').disabled = false; }
}
async function loadLogs() {
    logController?.abort(); const controller = new AbortController(); logController = controller;
    $('refreshLogs').disabled = true; $('downloadLogs').disabled = true;
    displayedLogs = null; $('logsStatus').textContent = 'Лог татаж байна…';
    try {
        const data = await jsonResponse(await fetch(`/api/pod-logs/${path}?${filters()}`, {signal:controller.signal}));
        if (logController !== controller) return;
        displayedLogs = data; setContainers(data.containers, data.container);
        $('logOutput').textContent = data.text || 'Шүүлтүүрт тохирох лог алга.';
        $('logsStatus').textContent = `${data.fetched_lines} мөрөөс ${data.matched_lines} таарлаа · ${data.previous ? 'Өмнөх' : 'Одоогийн'} container: ${data.container}` +
            (data.possibly_truncated ? ' · Мөр/1 MiB хязгаарт хүрсэн байж болно.' : '');
        $('downloadLogs').disabled = false;
    } catch (error) {
        if (error.name !== 'AbortError') { $('logsStatus').textContent = error.message; $('logOutput').textContent = ''; }
    } finally { if (logController === controller) $('refreshLogs').disabled = false; }
}
function download(text, filename, type) {
    const url = URL.createObjectURL(new Blob([text],{type}));
    const link = node('a'); link.href = url; link.download = filename;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function buildReport() {
    $('buildReport').disabled = true;
    $('logsStatus').textContent = 'Bug report-ийн шинэ мэдээлэл татаж байна…';
    try {
        const response = await fetch(`/api/bug-report/${path}?${filters()}`);
        if (!response.ok) { await jsonResponse(response); return; }
        $('reportText').value = await response.text(); $('reportStatus').textContent = '';
        $('reportModal').showModal(); $('logsStatus').textContent = 'Bug report бэлэн.';
    } catch (error) { $('logsStatus').textContent = error.message; }
    finally { $('buildReport').disabled = false; }
}
$('refreshDetails').addEventListener('click',loadDetails);
$('refreshLogs').addEventListener('click',loadLogs);
$('logFilters').addEventListener('submit',event => { event.preventDefault(); loadLogs(); });
$('downloadLogs').addEventListener('click',() => { if (displayedLogs) download(displayedLogs.text,`${namespace}-${pod}-${displayedLogs.container}.log`,'text/plain;charset=utf-8'); });
$('buildReport').addEventListener('click',buildReport);
$('closeReport').addEventListener('click',() => $('reportModal').close());
$('downloadReport').addEventListener('click',() => download($('reportText').value,`${namespace}-${pod}-bug-report.md`,'text/markdown;charset=utf-8'));
$('copyReport').addEventListener('click',async () => {
    try { await navigator.clipboard.writeText($('reportText').value); $('reportStatus').textContent = 'Хууллаа.'; }
    catch { $('reportText').focus(); $('reportText').select(); $('reportStatus').textContent = 'Ctrl+C дарж хуулна уу.'; }
});
loadDetails().then(loadLogs);
