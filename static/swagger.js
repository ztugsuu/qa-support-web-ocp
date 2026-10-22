'use strict';
(() => {
  const clusters = ['testocp', 'testmogul'];
  const names = {testocp: 'Unitel', testmogul: 'Mogul'};
  const rows = document.getElementById('swaggerRows');
  const status = document.getElementById('swaggerStatus');
  const search = document.getElementById('swaggerSearch');
  const namespace = document.getElementById('swaggerNamespace');
  let data = {}, errors = {}, controller;
  function render() {
    rows.replaceChildren();
    const services = new Set(clusters.flatMap(c => (data[c] || []).map(r => r.service)));
    const query = search.value.trim().toLowerCase();
    let count = 0;
    [...services].sort().forEach(service => {
      if (!service.toLowerCase().includes(query) && !clusters.some(c => (data[c] || []).some(r => r.service === service && r.name.toLowerCase().includes(query)))) return;
      count++;
      const tr = document.createElement('tr');
      const title = document.createElement('td');
      title.textContent = service;
      tr.append(title);
      clusters.forEach(c => {
        const cell = document.createElement('td');
        const matches = (data[c] || []).filter(r => r.service === service);
        matches.forEach(r => {
          const link = document.createElement('a');
          link.href = r.url;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.textContent = `${r.name} ↗`;
          link.title = r.url;
          cell.append(link, document.createElement('br'));
        });
        if (!matches.length) cell.textContent = errors[c] ? 'Мэдээлэл боломжгүй' : 'Route байхгүй';
        tr.append(cell);
      });
      rows.append(tr);
    });
    status.textContent = [...clusters.filter(c => errors[c]).map(c => `${names[c]}: ${errors[c]}`), `${count} сервис · ${namespace.value}`].join(' · ');
  }
  async function refresh() {
    if (controller) controller.abort();
    controller = new AbortController();
    const current = controller;
    data = {}; errors = {}; rows.replaceChildren();
    status.textContent = 'Route жагсаалтыг ачаалж байна…';
    await Promise.all(clusters.map(async c => {
      try {
        const response = await fetch(`/api/swagger-routes?cluster=${c}&namespace=${encodeURIComponent(namespace.value)}`, {signal: current.signal});
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Route авах боломжгүй.');
        if (current === controller) data[c] = body.routes;
      } catch (error) {
        if (current === controller && error.name !== 'AbortError') errors[c] = error.message;
      }
    }));
    if (current === controller) render();
  }
  search.addEventListener('input', render);
  namespace.addEventListener('change', refresh);
  document.getElementById('refreshSwagger').addEventListener('click', refresh);
  refresh();
})();
