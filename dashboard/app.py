"""MoneyGraph analyst workspace with a local SQLite workspace."""
import argparse
import os
from pathlib import Path
import sys
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.data import load_files_bundle, signature, node_warnings
from dashboard.graph import ego_edges, layout, figure
from dashboard.investigation import upstream, contributions, case_note
from dashboard.provenance import read_report
from dashboard.storage import open_store, StorageError
from dashboard.database import database_configured, list_snapshots, load_snapshot, DatabaseSourceError
from dashboard.auth import require_identity, account_controls
from dashboard.ai.ui import investigation_panel
from dashboard.data import ROOT
from dashboard.ui import apply_theme, brand, page_header, metrics, priority_table, NAV, ROLE_LABELS, ROLE_HINTS, readable, table_columns, quick_guide

parser = argparse.ArgumentParser()
parser.add_argument('--data', default='./data')
parser.add_argument('--out', default='./out')
args, _ = parser.parse_known_args()
st.set_page_config(page_title='Freedom Flow · Рабочее пространство', page_icon=str(Path(__file__).parent / 'frontend/freedom-logo.png'), layout='wide')
apply_theme()
try:
    store = open_store()
    identity = require_identity(store)
except StorageError:
    st.title('Рабочее пространство временно недоступно')
    st.error('Не удалось подключиться к хранилищу аккаунтов. Проверьте доступность MariaDB и настройки подключения.')
    if st.button('Повторить подключение', type='primary'):
        st.rerun()
    st.caption('Существующие аккаунты и заметки сохраняются в выбранном хранилище.')
    st.stop()
user_id = identity['id']
brand(sidebar=True)
section = st.sidebar.radio('Рабочее пространство', list(NAV), format_func=NAV.get, key='section')
with st.sidebar.expander('Источник данных', icon=':material/database:'):
    prefer_database = database_configured() and not os.environ.get('MONEYGRAPH_DB')
    configured_source = os.environ.get('MONEYGRAPH_DATA_SOURCE', '').lower()
    if configured_source in ('files', 'mariadb'):
        prefer_database = configured_source == 'mariadb'
    source = st.selectbox('Источник аналитики', ['Файлы CSV / Parquet', 'MariaDB'], index=int(prefer_database), key='data_source')
    data_dir = st.text_input('Каталог parquet', args.data, disabled=source == 'MariaDB')
    out_dir = st.text_input('Каталог CSV', args.out, disabled=source == 'MariaDB')
    st.caption('Для файлов: пути относительно корня проекта. MariaDB: снимки из настроенного сервера.')
try:
    account_controls(store, identity)
except StorageError:
    st.error('Не удалось выполнить действие с аккаунтом. Проверьте подключение и повторите.')
    st.stop()
st.sidebar.caption('Прототип команды · не официальный сервис банка')

def open_node(gid):
    st.session_state['node_gid'] = str(gid)
    st.session_state['section'] = 'Узел и переводы'

page_header(section, identity, 'MariaDB · аккаунты и заметки' if getattr(store, 'backend', '') == 'mariadb' else 'Локальное пространство')

@st.cache_data(show_spinner=False, max_entries=8)
def cached_bundle(data_dir, out_dir, version):
    return load_files_bundle(data_dir, out_dir)

@st.cache_data(show_spinner=False, max_entries=64)
def cached_layout(gid, edges):
    return layout(gid, edges)

@st.cache_data(show_spinner=False, ttl=60, max_entries=2)
def available_snapshots():
    return list_snapshots()


@st.cache_data(show_spinner='Загружаем и проверяем снимок MariaDB…', ttl=60, max_entries=4)
def database_bundle(run_id):
    return load_snapshot(run_id)


snapshot_id = None
dataset_id = None
files = {}
try:
    if source == 'MariaDB':
        snapshots = available_snapshots()
        if not snapshots:
            raise DatabaseSourceError('В MariaDB пока нет опубликованных аналитических снимков.')
        by_id = {item['run_id']: item for item in snapshots}
        with st.sidebar.expander('Снимок MariaDB', expanded=True):
            snapshot_id = st.selectbox('Версия аналитики', list(by_id),
                format_func=lambda value: f"{by_id[value]['created_at']} · {value[:8]}")
            if st.button('Обновить данные', icon=':material/refresh:'):
                available_snapshots.clear()
                database_bundle.clear()
                st.rerun()
        (nodes, clusters, top, edges), files = database_bundle(snapshot_id)
    else:
        (nodes, clusters, top, edges), files = cached_bundle(data_dir, out_dir, signature(data_dir, out_dir))
    dataset_id = store.snapshot(files)
except (OSError, ValueError, KeyError, TypeError, StorageError) as error:
    if section == 'Обзор':
        metrics()
        priority_table()
    st.info('Подключите данные, чтобы начать проверку. Укажите источник в боковой панели «Источник данных».')
    with st.expander('Подробности загрузки и команда расчёта', expanded=True):
        st.error(str(error) if isinstance(error, DatabaseSourceError) else 'Не удалось загрузить или сохранить набор. Проверьте файлы и подключение к хранилищу.')
        st.code('python run.py --data ./data --out ./out' + (' --mysql' if source == 'MariaDB' else ''), language='bash')
    if st.button('Повторить загрузку', icon=':material/refresh:'):
        available_snapshots.clear()
        database_bundle.clear()
        st.rerun()
    if section != 'Мои проверки':
        st.stop()

if section == 'Обзор':
    metrics([len(nodes), len(edges), int(edges.n_tx.sum()), len(clusters)])
    st.caption(f'Наблюдаемый оборот: {edges.sum_kzt.sum():,.2f} KZT · исходных участников: {nodes.is_seed.sum()} · изолированных: {(nodes.is_seed & nodes.in_deg.eq(0) & nodes.out_deg.eq(0)).sum()}')
    quick_guide()
    priority_table(top, nodes, dataset_id)
    with st.expander('Быстрый переход к узлу', expanded=True):
        st.subheader('С чего начать проверку', icon=':material/travel_explore:')
        st.write('Откройте приоритетный узел, изучите входящие связи и сохраните выводы в проверку.')
        focus = st.selectbox('Приоритетный узел', top.gid.tolist(), key='focus_gid')
        st.button('Исследовать узел', type='primary', icon=':material/arrow_forward:', on_click=open_node, args=(focus,))
    with st.expander('Исходная таблица и экспорт CSV'):
        st.caption('Полный список приоритетных узлов, без фильтров JavaScript-таблицы.')
        st.dataframe(top, hide_index=True, width='stretch', column_config={'gid':st.column_config.TextColumn('gid'), 'priority_score':st.column_config.ProgressColumn('Приоритет проверки',format='%.3f',min_value=0,max_value=1)})
        st.download_button('Скачать полный список CSV', top.to_csv(index=False).encode('utf-8'), 'top_nodes.csv', 'text/csv')
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader('Роли в наблюдаемом графе')
        st.bar_chart(nodes.role.map(ROLE_LABELS).value_counts().rename('Узлы'), color='#19ac99')
    with right, st.container(border=True):
        st.subheader('Глубина наблюдения')
        st.bar_chart(nodes.depth.value_counts().sort_index().rename('Узлы'), color='#365d78')
    st.subheader('Ограничения выборки')
    st.write('Граф построен по исходящим переводам от 81 исходного участника (seed), до четырёх шагов. Входящие переводы исходных участников неполны; исходящие за границей глубины неизвестны. Все суммы относятся только к наблюдаемой выборке. В транзакциях есть дата, но нет времени суток: совпадение дня не доказывает порядок и путь конкретных денег.')
elif section == 'Узел и переводы':
    st.session_state.setdefault('node_gid', str(top.iloc[0].gid))
    gid = st.text_input('Поиск по полному gid', key='node_gid', help='Скопируйте ID участника из таблицы. gid — его идентификатор в исходной выборке.', placeholder='Введите ID участника').strip()
    matches = nodes[nodes.gid.eq(gid)]
    if matches.empty:
        st.warning('gid не найден. Введите полный целочисленный идентификатор из таблицы.')
        st.button('Открыть первого в списке приоритета', on_click=open_node, args=(str(top.iloc[0].gid),))
        st.stop()
    row = matches.iloc[0]
    if st.session_state.get('last_view') != (user_id, dataset_id, gid):
        try:
            store.event(user_id, 'Открыт узел', gid)
            st.session_state['last_view'] = (user_id, dataset_id, gid)
        except StorageError:
            st.warning('История просмотра временно недоступна. Аналитику можно продолжить.')
    st.subheader(f'gid {gid}')
    cols = st.columns(3)
    cols[0].metric('Роль · гипотеза', ROLE_LABELS.get(row.role, row.role), help=ROLE_HINTS.get(row.role))
    cols[1].metric('Сила правила', f'{row.role_score:.3f}', help='Насколько выражены признаки выбранной роли по нашему правилу. Не вероятность.')
    cols[2].metric('Приоритет проверки', f'{row.priority_score:.3f}', help='Очередность ручной проверки от 0 до 1. Не вероятность нарушения.')
    incoming = f'{row.in_kzt:,.2f}'.replace(',', ' ')
    outgoing = f'{row.out_kzt:,.2f}'.replace(',', ' ')
    st.write(f'Получил **{incoming} ₸** от **{int(row.in_deg)}** участников. '
             f'Отправил **{outgoing} ₸** **{int(row.out_deg)}** получателям.')
    st.caption('Суммы и связи относятся только к наблюдаемой выборке.')
    for warning in node_warnings(row):
        st.warning(warning)
    with st.expander('Исходное объяснение из расчёта'):
        st.text(row.evidence)
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
            st.dataframe(readable(candidates), hide_index=True, width='stretch', column_config=table_columns())
        st.caption('Путь в графе не доказывает движение одной суммы или общее управление участниками.')
        st.download_button('Скачать записку для проверки', case_note(row, candidates), f'case_{gid}.md', 'text/markdown')
    st.caption(f'Кластер {row.cluster_id} · глубина {int(row.depth)} · вход {row.in_kzt:,.2f} KZT от {int(row.in_deg)} · выход {row.out_kzt:,.2f} KZT к {int(row.out_deg)} · pass_through {row.pass_through:.3f}')
    if 'fast2' in row:
        with st.expander('Как читать признаки потока'):
            st.write(f'Быстрое сопоставление сумм (fast2): {float(row.fast2):.3f}. Входы и выходы сопоставляются за 0–2 календарных дня.')
            st.caption('В данных есть только даты. Порядок переводов внутри дня и путь конкретных денег не доказаны. Для исходных участников входы неполны.')
    st.subheader('Направленные переводы · один шаг')
    controls = st.columns(3)
    limit = controls[0].slider('Лимит соседей', 1, 60, 30, key=f'neighbors_{gid}')
    minimum = controls[1].number_input('Минимум на пару, KZT', min_value=0.0, value=0.0, step=5000.0, key=f'minimum_{gid}')
    direction = controls[2].selectbox('Направление', ['both', 'in', 'out'], key=f'direction_{gid}', format_func=lambda x: {'both':'Оба', 'in':'Входящие', 'out':'Исходящие'}[x])
    selected, available = ego_edges(edges, gid, limit, minimum, direction)
    st.caption(f'Показано {min(available, limit)} из {available} соседей после фильтра; {len(selected)} направленных пар. Соседи отобраны по сумме переводов. Стрелка ведёт от плательщика к получателю; сумма — при наведении на середину ребра и в таблице.')
    if selected.empty:
        st.info('Нет наблюдаемых переводов для выбранных фильтров.')
    st.caption('Крупный узел — выбранный участник. Наведите на узел для роли и приоритета; на середину связи — для суммы.')
    st.plotly_chart(figure(gid, selected, nodes, cached_layout(gid, selected)), width='stretch')
    st.dataframe(selected, hide_index=True, width='stretch', column_config=table_columns())
    with st.expander('Все наблюдаемые переводы выбранного узла, без фильтра графа'):
        st.dataframe(edges[edges.src.eq(gid) | edges.dst.eq(gid)], hide_index=True, width='stretch', column_config=table_columns())
    try:
        saved = store.case(user_id, dataset_id, gid)
    except StorageError:
        st.error('Не удалось прочитать сохранённую заметку. Повторите загрузку перед редактированием.')
        st.stop()
    with st.expander('Сохранить проверку', expanded=True, icon=':material/bookmark:'):
        with st.form(f'case_{dataset_id}_{gid}'):
            statuses = ['В работе', 'Проверено', 'Отложено']
            status = st.selectbox('Статус проверки', statuses, index=statuses.index(saved['status']) if saved else 0)
            note = st.text_area('Заметка аналитика', value=saved['note'] if saved else '', max_chars=10000, placeholder='Что наблюдаете и что необходимо проверить дальше?')
            if st.form_submit_button('Сохранить в мои проверки', type='primary', icon=':material/save:'):
                try:
                    store.save_case(user_id, dataset_id, gid, status, note, case_note(row, candidates))
                except StorageError:
                    st.error('Не удалось сохранить проверку. Текст остаётся в форме — повторите попытку.')
                else:
                    st.success('Проверка сохранена. Она будет доступна после перезапуска приложения.')
    investigation_panel(gid, files, store, user_id, dataset_id)
elif section == 'Кластеры':
    cluster_id = st.selectbox('Кластер', clusters.cluster_id.tolist(), format_func=lambda value: f'Кластер {value} · {int(clusters.loc[clusters.cluster_id.eq(value), "n_nodes"].iloc[0])} узлов')
    row = clusters[clusters.cluster_id.eq(cluster_id)].iloc[0]
    cols = st.columns(3)
    cols[0].metric('Узлы', int(row.n_nodes))
    cols[1].metric('Исходные участники', int(row.n_seed))
    cols[2].metric('Внутренний оборот, KZT', f'{row.sum_kzt_internal:,.2f}')
    st.write(row.hypothesis)
    st.caption('Кластеры вычислены на ненаправленной проекции с суммой обоих направлений. Принадлежность группе не доказывает общее назначение переводов.')
    st.write('**Участники для начала проверки:**', row.top_gids)
    members = nodes[nodes.cluster_id.eq(cluster_id)].sort_values(['priority_score','gid'],ascending=[False,True])
    st.dataframe(readable(members[['gid','role','role_score','priority_score','evidence','depth','is_seed']]), hide_index=True, width='stretch', column_config=table_columns())
    member = st.selectbox('Участник кластера', members.gid.tolist())
    st.button('Исследовать участника', type='primary', on_click=open_node, args=(member,))
elif section == 'Мои проверки':
    try:
        records = store.cases(user_id)
    except StorageError:
        st.error('Не удалось загрузить проверки. Повторите после восстановления подключения.')
        st.stop()
    st.caption('Личные заметки, статусы и снимки аналитических записок. Каждая проверка связана с версией данных.')
    if not records:
        st.info('Пока нет сохранённых проверок. Откройте узел и нажмите «Сохранить в мои проверки».')
    if records:
        status_filter = st.selectbox('Показать проверки', ['Все статусы', 'В работе', 'Проверено', 'Отложено'])
        query = st.text_input('Найти в моих проверках', placeholder='ID участника или текст заметки').strip().lower()
        records = [r for r in records if (status_filter == 'Все статусы' or r['status'] == status_filter) and (not query or query in r['gid'] or query in r['note'].lower())]
        st.caption(f'Найдено проверок: {len(records)}')
        if not records:
            st.info('Нет проверок с такими условиями. Измените статус или поисковый запрос.')
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
        try:
            history = store.history(user_id)
            st.dataframe(pd.DataFrame(history), hide_index=True, width='stretch')
        except StorageError:
            st.warning('История временно недоступна.')
else:
    st.subheader('Паспорт расчёта')
    st.caption(f'Источник: {source} · версия данных: {dataset_id[:12]}')
    with st.container(horizontal=True):
        for name in ('nodes_roles', 'clusters', 'top_nodes'):
            st.download_button(f'{name}.csv', files[f'out/{name}.csv'], f'{name}.csv', 'text/csv')
    if source == 'MariaDB':
        st.success('Снимок прочитан из MariaDB. Структура, строки и контрольные суммы проверены при загрузке.')
        st.code(snapshot_id, language=None)
        st.caption('Паспорт времени и диагностики локального запуска не входит в снимок MariaDB. Для него выберите соответствующие локальные файлы.')
        st.stop()
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
