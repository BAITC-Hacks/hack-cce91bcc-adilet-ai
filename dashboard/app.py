"""MoneyGraph analyst workspace with a local SQLite workspace."""
import argparse
import os
from pathlib import Path
import sys
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.data import load_bundle, signature, node_warnings
from dashboard.graph import ego_edges, layout, figure
from dashboard.investigation import upstream, contributions, case_note
from dashboard.provenance import read_report
from dashboard.storage import Store
from dashboard.auth import require_identity, account_controls
from dashboard.data import ROOT, resolve_path
from dashboard.ai.ui import investigation_panel

parser = argparse.ArgumentParser()
parser.add_argument('--data', default='./data')
parser.add_argument('--out', default='./out')
args, _ = parser.parse_known_args()
st.set_page_config(page_title='MoneyGraph · Рабочее пространство', page_icon=':material/hub:', layout='wide')
store = Store(os.environ.get('MONEYGRAPH_DB', str(ROOT / 'data/moneygraph.sqlite3')))
identity = require_identity(store)
user_id = identity['id']
st.sidebar.title('MoneyGraph')
st.sidebar.caption('FREEDOM STYLE · HACKALEM AI')
section = st.sidebar.radio('Раздел', ['Обзор', 'Узел и переводы', 'Кластеры', 'Мои проверки', 'Проверяемость'], key='section')
with st.sidebar.expander('Источник данных', icon=':material/database:'):
    data_dir = st.text_input('Каталог parquet', args.data)
    out_dir = st.text_input('Каталог CSV', args.out)
    st.caption('Пути относительно корня проекта.')
account_controls(store, identity)
st.sidebar.caption('Прототип команды · не официальный сервис банка')

def open_node(gid):
    st.session_state['node_gid'] = str(gid)
    st.session_state['section'] = 'Узел и переводы'

st.badge('Рабочее пространство аналитика', color='green', icon=':material/hub:')
st.title({'Обзор': 'Вся картина движения денег', 'Узел и переводы': 'От перевода к связям', 'Кластеры': 'Связанные сообщества', 'Мои проверки': 'Ваши проверки', 'Проверяемость': 'Данные, которым можно задать вопрос'}[section])
st.caption('Июль 2026 · наблюдаемые переводы · гипотезы для проверки')

@st.cache_data(show_spinner=False, max_entries=8)
def cached_bundle(data_dir, out_dir, version):
    return load_bundle(data_dir, out_dir)

@st.cache_data(show_spinner=False, max_entries=64)
def cached_layout(gid, edges):
    return layout(gid, edges)

try:
    nodes, clusters, top, edges = cached_bundle(data_dir, out_dir, signature(data_dir, out_dir))
    files = {f'data/{name}.parquet': (resolve_path(data_dir) / f'{name}.parquet').read_bytes() for name in ('nodes', 'edges', 'transactions')}
    files.update({f'out/{name}.csv': (resolve_path(out_dir) / f'{name}.csv').read_bytes() for name in ('nodes_roles', 'clusters', 'top_nodes')})
    dataset_id = store.snapshot(files)
except (OSError, ValueError, KeyError, TypeError) as error:
    st.error(f'Не удалось загрузить данные: {error}')
    st.info('Проверьте пути и сформируйте три CSV из исходного датасета:')
    st.code('python run.py --data ./data --out ./out', language='bash')
    st.stop()

st.caption('Роль — гипотеза. Приоритет проверки и сила правила не являются вероятностью правонарушения.')
if section == 'Обзор':
    columns = st.columns(4)
    for col, label, value in zip(columns, ['Узлы', 'Направленные пары', 'Переводы', 'Кластеры'], [len(nodes), len(edges), int(edges.n_tx.sum()), len(clusters)]):
        with col.container(border=True):
            st.metric(label, f'{value:,}'.replace(',', ' '))
    st.caption(f'Наблюдаемый оборот: {edges.sum_kzt.sum():,.2f} KZT · seed: {nodes.is_seed.sum()} · изолированные seed: {(nodes.is_seed & nodes.in_deg.eq(0) & nodes.out_deg.eq(0)).sum()}')
    with st.container(border=True):
        st.subheader('С чего начать проверку', icon=':material/travel_explore:')
        st.write('Откройте приоритетный узел, изучите входящие связи и сохраните выводы в проверку.')
        focus = st.selectbox('Приоритетный узел', top.gid.tolist(), key='focus_gid')
        st.button('Исследовать узел', type='primary', icon=':material/arrow_forward:', on_click=open_node, args=(focus,))
    st.subheader('Приоритет проверки')
    st.caption('Выше — узлы с большим приоритетом ручной проверки. Сортировка доступна по заголовкам.')
    st.dataframe(top, hide_index=True, width='stretch', column_config={'gid':st.column_config.TextColumn('gid'), 'priority_score':st.column_config.ProgressColumn('Приоритет проверки',format='%.3f',min_value=0,max_value=1)})
    st.download_button('Скачать показанную таблицу', top.to_csv(index=False).encode('utf-8'), 'top_nodes.csv', 'text/csv')
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader('Роли в наблюдаемом графе')
        st.bar_chart(nodes.role.value_counts().rename('Узлы'), color='#247A38')
    with right, st.container(border=True):
        st.subheader('Глубина наблюдения')
        st.bar_chart(nodes.depth.value_counts().sort_index().rename('Узлы'), color='#83BA3B')
    st.subheader('Ограничения выборки')
    st.write('Граф построен по исходящим переводам от seed до 4 колен. Входящие seed неполны; исходящие за границей глубины неизвестны. Все суммы относятся только к наблюдаемой выборке. В транзакциях есть дата, но нет времени суток: совпадение дня не доказывает порядок и путь конкретных денег.')
elif section == 'Узел и переводы':
    st.session_state.setdefault('node_gid', str(top.iloc[0].gid))
    gid = st.text_input('Поиск по полному gid', key='node_gid').strip()
    matches = nodes[nodes.gid.eq(gid)]
    if matches.empty:
        st.warning('gid не найден. Введите полный целочисленный идентификатор из таблицы.')
        st.stop()
    row = matches.iloc[0]
    if st.session_state.get('last_view') != (user_id, dataset_id, gid):
        store.event(user_id, 'Открыт узел', gid)
        st.session_state['last_view'] = (user_id, dataset_id, gid)
    st.subheader(f'gid {gid}')
    cols = st.columns(3)
    cols[0].metric('Роль · гипотеза', row.role)
    cols[1].metric('Сила правила', f'{row.role_score:.3f}')
    cols[2].metric('Приоритет проверки', f'{row.priority_score:.3f}')
    st.write('**Наблюдения:**', row.evidence)
    parts = contributions(row)
    if not parts.empty:
        with st.expander('Почему такой приоритет: вклад каждого признака'):
            st.bar_chart(parts.set_index('Компонента'))
            st.dataframe(parts, hide_index=True, width='stretch')
            st.caption('Вклады суммируются в priority_score. Это объяснение правила, а не причинности или вероятности.')
    with st.container(border=True):
        st.subheader('Кто выше по наблюдаемой цепочке')
        st.caption('Обратный поиск до 4 шагов: кто может достичь выбранного узла по направлению переводов. В таблице один кратчайший путь на кандидата, до 20 кандидатов по приоритету.')
        depth = st.slider('Глубина поиска предшественников', 1, 4, 4)
        candidates = upstream(nodes, edges, gid, cutoff=depth)
        if candidates.empty:
            st.info('В выбранной глубине предшественников не найдено. Это не исключает внешние источники вне выборки.')
        else:
            st.dataframe(candidates, hide_index=True, width='stretch')
        st.caption('Путь в графе не доказывает движение одной суммы или общее управление участниками.')
        st.download_button('Скачать записку для проверки', case_note(row, candidates), f'case_{gid}.md', 'text/markdown')
    saved = store.case(user_id, dataset_id, gid)
    with st.expander('Сохранить проверку', expanded=True, icon=':material/bookmark:'):
        with st.form(f'case_{dataset_id}_{gid}'):
            statuses = ['В работе', 'Проверено', 'Отложено']
            status = st.selectbox('Статус проверки', statuses, index=statuses.index(saved['status']) if saved else 0)
            note = st.text_area('Заметка аналитика', value=saved['note'] if saved else '', max_chars=10000, placeholder='Что наблюдаете и что необходимо проверить дальше?')
            if st.form_submit_button('Сохранить в мои проверки', type='primary', icon=':material/save:'):
                store.save_case(user_id, dataset_id, gid, status, note, case_note(row, candidates))
                st.success('Проверка сохранена. Она будет доступна после перезапуска приложения.')
    for warning in node_warnings(row):
        st.warning(warning)
    st.caption(f'Кластер {row.cluster_id} · глубина {int(row.depth)} · вход {row.in_kzt:,.2f} KZT от {int(row.in_deg)} · выход {row.out_kzt:,.2f} KZT к {int(row.out_deg)} · pass_through {row.pass_through:.3f}')
    if 'fast2' in row:
        st.info(f'fast2 = {float(row.fast2):.3f}: возможное сопоставление сумм за 0–2 календарных дня. Только дневные даты; порядок переводов внутри дня и путь конкретных денег не доказаны.')
    st.subheader('Направленные переводы · один шаг')
    controls = st.columns(3)
    limit = controls[0].slider('Лимит соседей', 1, 60, 30)
    minimum = controls[1].number_input('Минимум на пару, KZT', min_value=0.0, value=0.0, step=5000.0)
    direction = controls[2].selectbox('Направление', ['both', 'in', 'out'], format_func=lambda x: {'both':'Оба', 'in':'Входящие', 'out':'Исходящие'}[x])
    selected, available = ego_edges(edges, gid, limit, minimum, direction)
    st.caption(f'Показано {min(available, limit)} из {available} соседей после фильтра; {len(selected)} направленных пар. Соседи отобраны по сумме, layout seed=42. Стрелка ведёт от плательщика к получателю; сумма — при наведении на середину ребра и в таблице.')
    if selected.empty:
        st.info('Нет наблюдаемых переводов для выбранных фильтров.')
    st.plotly_chart(figure(gid, selected, nodes, cached_layout(gid, selected)), width='stretch')
    st.dataframe(selected, hide_index=True, width='stretch')
    with st.expander('Все наблюдаемые переводы выбранного узла, без фильтра графа'):
        st.dataframe(edges[edges.src.eq(gid) | edges.dst.eq(gid)], hide_index=True, width='stretch')
    investigation_panel(gid, files, store, user_id, dataset_id)
elif section == 'Кластеры':
    cluster_id = st.selectbox('Кластер', clusters.cluster_id.tolist(), format_func=lambda value: f'Кластер {value} · {int(clusters.loc[clusters.cluster_id.eq(value), "n_nodes"].iloc[0])} узлов')
    row = clusters[clusters.cluster_id.eq(cluster_id)].iloc[0]
    cols = st.columns(3)
    cols[0].metric('Узлы', int(row.n_nodes))
    cols[1].metric('Seed', int(row.n_seed))
    cols[2].metric('Внутренний оборот, KZT', f'{row.sum_kzt_internal:,.2f}')
    st.write(row.hypothesis)
    st.caption('Кластеры вычислены на ненаправленной проекции с суммой обоих направлений. Принадлежность группе не доказывает общее назначение переводов.')
    st.write('**Приоритетные gid:**', row.top_gids)
    members = nodes[nodes.cluster_id.eq(cluster_id)].sort_values(['priority_score','gid'],ascending=[False,True])
    st.dataframe(members[['gid','role','role_score','priority_score','evidence','depth','is_seed']], hide_index=True, width='stretch')
elif section == 'Мои проверки':
    records = store.cases(user_id)
    st.caption('Личные заметки, статусы и снимки аналитических записок. Каждая проверка связана с версией данных.')
    if not records:
        st.info('Пока нет сохранённых проверок. Откройте узел и нажмите «Сохранить в мои проверки».')
    for record in records:
        with st.container(border=True):
            st.subheader(f"gid {record['gid']}")
            st.badge(record['status'], color='green' if record['status'] == 'Проверено' else 'gray')
            st.caption(f"Обновлено {record['updated_at']} UTC · версия {record['dataset_id'][:12]}")
            st.text(record['note'] or 'Заметка пока не добавлена.')
            st.download_button('Скачать сохранённую записку', record['report'], f"case_{record['gid']}.md", 'text/markdown', key=f"download_{record['id']}")
            if record['dataset_id'] == dataset_id:
                st.button('Открыть и изменить', key=f"open_{record['id']}", on_click=open_node, args=(record['gid'],))
            else:
                st.caption('Архивная версия данных. Сохранённая записка доступна для скачивания.')
    with st.expander('История действий', icon=':material/history:'):
        st.dataframe(pd.DataFrame(store.history(user_id)), hide_index=True, width='stretch')
else:
    st.subheader('Паспорт расчёта')
    st.caption('Связывает показанные CSV с конкретным запуском. Хеши проверяют совпадение файлов, а не достоверность исходных сведений.')
    try:
        report = read_report(out_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        st.warning(f'Паспорт недоступен или не соответствует CSV: {error}')
        st.code('python run.py --data ./data --out ./out')
    else:
        st.success('Хеши трёх CSV совпадают с паспортом запуска.')
        st.write('Версия алгоритма:', report['algorithm'])
        diagnostics = report['diagnostics']
        st.write(f"Совпадение топ-20 с простым рейтингом по максимальному входу/выходу: {diagnostics['volume_baseline_top20_overlap']} из 20.")
        st.caption('Различие с рейтингом по сумме показывает влияние структуры и временных признаков. Оно не доказывает более высокую точность.')
        sensitivity = pd.DataFrame(list(diagnostics['leave_one_component_out_top20_overlap'].items()), columns=['Исключённый компонент', 'Сохранились в топ-20'])
        st.dataframe(sensitivity, hide_index=True, width='stretch')
        st.caption('Чувствительность: исключаем один вклад, остальные веса не меняем. Чем меньше совпадение, тем сильнее рейтинг зависит от компонента.')
        with st.expander('Параметры, версии и контрольные суммы'):
            st.json(report)
