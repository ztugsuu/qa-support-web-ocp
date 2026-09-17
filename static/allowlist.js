'use strict';
const byId = id => document.getElementById(id);
let page = 1, listRequest, searchTimer;
function node(tag, text, className) {
    const result = document.createElement(tag);
    result.textContent = text;
    if (className) result.className = className;
    return result;
}
async function loadEntries() {
    listRequest?.abort();
    const controller = new AbortController(); listRequest = controller;
    const environment = byId('listEnvironment').value, kind = byId('listKind').value;
    byId('valueHeading').textContent = kind === 'phone' ? 'Утасны дугаар' : 'Email хаяг';
    byId('entriesBody').replaceChildren();
    byId('listEmpty').hidden = false; byId('listEmpty').textContent = 'Жагсаалт ачаалж байна…';
    byId('listError').hidden = true; byId('listRefresh').disabled = true;
    byId('previousPage').disabled = true; byId('nextPage').disabled = true;
    byId('listSummary').textContent = ''; byId('pageNumber').textContent = page;
    try {
        const params = new URLSearchParams({q: byId('listSearch').value.trim(), page});
        const response = await fetch(`/api/allowlist/${environment}/${kind}?${params}`, {signal: controller.signal});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Жагсаалт татаж чадсангүй.');
        data.items.forEach((value, index) => {
            const row = node('tr', '');
            row.append(node('td', (page - 1) * data.page_size + index + 1), node('td', value, 'allowlist-value'), node('td', environment === 'test' ? 'Test' : 'Pre'));
            byId('entriesBody').append(row);
        });
        byId('listEmpty').hidden = data.items.length > 0;
        byId('listEmpty').textContent = byId('listSearch').value.trim() ? 'Хайлтад тохирох бүртгэл олдсонгүй.' : 'Бүртгэл алга.';
        byId('listSummary').textContent = `${environment === 'test' ? 'Test' : 'Pre'} · Нийт ${data.total} бүртгэл · Хуудас бүрд 50`;
        byId('previousPage').disabled = page <= 1;
        byId('nextPage').disabled = page * data.page_size >= data.total;
    } catch (error) {
        if (error.name === 'AbortError') return;
        byId('listError').textContent = error.message; byId('listError').hidden = false;
        byId('listEmpty').textContent = 'Мэдээлэл боломжгүй байна. Шинэчлэх товчоор дахин оролдоно уу.';
    } finally { if (listRequest === controller) byId('listRefresh').disabled = false; }
}
byId('addForm').addEventListener('submit', async event => {
    event.preventDefault();
    const form = byId('addForm');
    if (!form.reportValidity()) return;
    const environments = Array.from(form.querySelectorAll('[name="environments"]:checked'), input => input.value);
    const email = byId('emailInput').value.trim(), phone = byId('phoneInput').value.trim();
    const results = byId('addResults'); results.replaceChildren();
    if (!environments.length || (!email && !phone)) {
        results.append(node('p', 'Нэмэх орчин болон email эсвэл дугаараа оруулна уу.', 'error-banner')); return;
    }
    const controls = Array.from(form.querySelectorAll('input, button'));
    controls.forEach(input => input.disabled = true);
    byId('addButton').textContent = 'Нэмж байна…';
    results.append(node('p', 'Сонгосон орчинд бүртгэл шалгаж байна…', 'muted'));
    try {
        const response = await fetch('/api/allowlist', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-QA-Request': 'allowlist'}, body: JSON.stringify({environments, email, phone})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Нэмэх хүсэлт амжилтгүй.');
        results.replaceChildren();
        for (const [environment, result] of Object.entries(data.results)) {
            const label = environment === 'test' ? 'Test' : 'Pre';
            if (!result.ok) { results.append(node('p', `${label}: ${result.error} Энэ орчинд нэмэлт хадгалагдаагүй.`, 'error-banner')); continue; }
            for (const [kind, status] of Object.entries(result.entries)) {
                results.append(node('p', `${label} · ${kind === 'email' ? email : phone}: ${status === 'added' ? 'Амжилттай нэмэгдлээ' : 'Өмнө бүртгэгдсэн байна'}`, 'success-banner'));
            }
        }
        await loadEntries();
    } catch (error) {
        results.replaceChildren(node('p', `${error.message} Үр дүн тодорхойгүй бол жагсаалтаас шалгах эсвэл дахин оролдоно уу. Давхар нэмэхгүй.`, 'error-banner'));
    } finally {
        controls.forEach(input => input.disabled = false); byId('addButton').textContent = '＋ Жагсаалтад нэмэх';
    }
});
['listEnvironment','listKind'].forEach(id => byId(id).addEventListener('change', () => { clearTimeout(searchTimer); page = 1; loadEntries(); }));
byId('listSearch').addEventListener('input', () => {
    clearTimeout(searchTimer); listRequest?.abort();
    byId('previousPage').disabled = true; byId('nextPage').disabled = true;
    searchTimer = setTimeout(() => { page = 1; loadEntries(); }, 300);
});
byId('listRefresh').addEventListener('click', () => { clearTimeout(searchTimer); loadEntries(); });
byId('previousPage').addEventListener('click', () => { page--; loadEntries(); });
byId('nextPage').addEventListener('click', () => { page++; loadEntries(); });
loadEntries();
