// Local presentation only: no HTTP requests, credentials, or browser storage.
export default function ({ parentElement, data, setTriggerValue }) {
  const root = parentElement;
  const rows = data.rows || [];
  const labels = { consolidator: 'Сборщик средств', transit: 'Транзитный участник', distributor: 'Распределитель', terminal: 'Конечный получатель', coordinator: 'Координатор связей', peripheral: 'Периферийный участник' };
  const search = root.querySelector('input');
  const select = root.querySelector('select');
  const body = root.querySelector('tbody');
  const empty = root.querySelector('.empty');
  const prev = root.querySelector('.prev');
  const next = root.querySelector('.next');
  const reset = root.querySelector('.reset');
  const size = 6;
  let page = 0, sort = 'score', direction = -1;
  const money = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
  const score = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  // Component code can be rerun; rebuild only its own option list and content.
  select.replaceChildren(new Option('Все роли', ''));
  [...new Set(rows.map(row => row.role))].sort().forEach(role => select.add(new Option(labels[role] || role, role)));
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function open(gid) { setTriggerValue('open_gid', String(gid)); }
  function render() {
    const query = search.value.trim();
    const filtered = rows.filter(row => row.gid.includes(query) && (!select.value || row.role === select.value))
      .sort((a, b) => direction * (a[sort] - b[sort]) || a.rank - b.rank);
    const pages = Math.max(1, Math.ceil(filtered.length / size));
    page = Math.min(page, pages - 1);
    body.replaceChildren();
    filtered.slice(page * size, (page + 1) * size).forEach(row => {
      const tr = element('tr');
      const id = element('td');
      const link = element('button', 'gid', row.gid);
      link.type = 'button';
      link.setAttribute('aria-label', `Открыть узел ${row.gid}`);
      link.onclick = () => open(row.gid);
      id.append(link, element('span', 'sub', `Позиция ${row.rank}`));
      const role = element('td');
      role.append(element('span', 'role', labels[row.role] || row.role));
      const amount = element('td', 'money', `₸ ${money.format(row.amount)}`);
      amount.append(element('span', 'sub', `Исходящие: ₸ ${money.format(row.outgoing)}`));
      const priority = element('td');
      const wrapper = element('div', 'score');
      const track = element('span', 'track');
      track.setAttribute('aria-hidden', 'true');
      const fill = element('i');
      fill.style.width = `${Math.min(1, Math.max(0, row.score)) * 100}%`;
      track.append(fill);
      wrapper.append(element('span', '', score.format(row.score)), track);
      priority.append(wrapper);
      priority.title = row.why;
      const action = element('td');
      const button = element('button', 'open', 'Открыть →');
      button.type = 'button';
      button.setAttribute('aria-label', `Исследовать узел ${row.gid}`);
      button.onclick = () => open(row.gid);
      action.append(button);
      tr.append(id, role, amount, priority, action);
      body.append(tr);
    });
    empty.hidden = filtered.length > 0;
    empty.querySelector('h3').textContent = rows.length ? 'Узлы не найдены' : 'Здесь появятся узлы для проверки';
    empty.querySelector('p').textContent = rows.length ? 'Измените ID или выберите другую роль.' : 'Подключите исходный набор и результаты расчёта в разделе «Источник данных».';
    reset.hidden = rows.length === 0;
    root.querySelector('.count').textContent = filtered.length ? `${page * size + 1}–${Math.min((page + 1) * size, filtered.length)} из ${filtered.length} узлов` : 'Нет записей';
    root.querySelector('.page').textContent = `${page + 1} / ${pages}`;
    prev.disabled = page === 0;
    next.disabled = page >= pages - 1;
    root.querySelectorAll('th[data-sort]').forEach(th => {
      const active = th.dataset.sort === sort;
      th.setAttribute('aria-sort', active ? (direction === -1 ? 'descending' : 'ascending') : 'none');
      th.querySelector('span').textContent = active ? (direction === -1 ? '↓' : '↑') : '↕';
    });
  }
  search.oninput = select.onchange = () => { page = 0; render(); };
  prev.onclick = () => { page--; render(); };
  next.onclick = () => { page++; render(); };
  reset.onclick = () => { search.value = ''; select.value = ''; page = 0; render(); search.focus(); };
  root.querySelectorAll('th[data-sort]').forEach(th => {
    th.querySelector('button').onclick = () => {
      direction = sort === th.dataset.sort ? -direction : -1;
      sort = th.dataset.sort;
      page = 0;
      render();
    };
  });
  render();
  return () => {
    search.oninput = select.onchange = prev.onclick = next.onclick = reset.onclick = null;
    root.querySelectorAll('th button').forEach(button => { button.onclick = null; });
  };
}
