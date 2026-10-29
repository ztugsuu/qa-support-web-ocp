'use strict';
(() => {
  const clusters = ['testocp', 'testmogul'];
  const labels = {testocp: 'Unitel', testmogul: 'Mogul'};
  const rows = document.getElementById('swaggerRows');
  const status = document.getElementById('swaggerStatus');
  const search = document.getElementById('swaggerSearch');
  const namespace = document.getElementById('swaggerNamespace');
  let data = {}, controller, timer;
  function render() {
    rows.replaceChildren();
    const services = new Set(clusters.flatMap(c => (data[c]?.routes || []).map(r => r.service)));
    const query = search.value.trim().toLowerCase();
    let count = 0;
    [...services].sort().forEach(service => {
      if (!service.toLowerCase().includes(query) && !clusters.some(c => (data[c]?.routes || []).some(r => r.service === service && r.name.toLowerCase().includes(query)))) return;
      count++;
      const tr = document.createElement('tr');
      const title = document.createElement('td'); title.textContent = service; tr.append(title);
      clusters.forEach(c => {
        const cell = document.createElement('td');
        const matches = (data[c]?.routes || []).filter(r => r.service === service);
        matches.forEach(r => {
          const link = document.createElement('a');
          link.href = r.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
          link.textContent = `${r.name} ↗`; link.title = r.url;
          cell.append(link, document.createElement('br'));
        });
        if (!matches.length) cell.textContent = data[c]?.error ? 'Мэдээлэл боломжгүй' : data[c]?.refreshing && !data[c]?.updated_at ? 'Ачаалж байна…' : 'Route байхгүй';
        tr.append(cell);
      });
      rows.append(tr);
    });
    status.textContent = [`${count} сервис · ${namespace.value}`, ...clusters.map(c => {
      const d = data[c];
      if (!d) return `${labels[c]}: ачаалж байна…`;
      if (d.error) return `${labels[c]}: ${d.error}${d.updated_at ? ' Өмнөх жагсаалтыг харуулж байна.' : ''}`;
      return `${labels[c]}: ${d.refreshing ? 'шинэчилж байна…' : d.updated_at ? new Date(d.updated_at).toLocaleString() : 'ачаалж байна…'}`;
    })].join(' · ');
  }
  async function refresh(attempt = 0) {
    clearTimeout(timer);
    controller?.abort();
    controller = new AbortController();
    const current = controller;
    const timeout = setTimeout(() => current.abort(), 10000);
    await Promise.all(clusters.map(async c => {
      try {
        const response = await fetch(`/api/swagger-routes?cluster=${c}&namespace=${encodeURIComponent(namespace.value)}`, {signal: current.signal});
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Мэдээлэл авах боломжгүй.');
        if (current === controller) data[c] = body;
      } catch (error) {
        if (current === controller) data[c] = {...data[c], refreshing: false, error: error.name === 'AbortError' ? 'Хүсэлтийн хугацаа дууслаа. Дахин шинэчилнэ үү.' : error.message};
      }
    }));
    clearTimeout(timeout);
    if (current !== controller) return;
    render();
    if (attempt < 8 && clusters.some(c => data[c]?.refreshing)) timer = setTimeout(() => refresh(attempt + 1), 2000);
  }
  search.addEventListener('input', render);
  namespace.addEventListener('change', () => { data = {}; rows.replaceChildren(); refresh(); });
  document.getElementById('refreshSwagger').addEventListener('click', () => refresh());
  refresh();
})();
