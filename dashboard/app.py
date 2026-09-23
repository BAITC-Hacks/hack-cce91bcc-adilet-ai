"""MoneyGraph analyst workspace with a local SQLite workspace."""
import argparse
import os
from pathlib import Path
import sys
import pandas as pd
import streamlit as st
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.i18n import t, html_t, translator, language, language_picker
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
with st.container(key='account_toolbar'):
    title_slot, language_slot, account_slot = st.columns([6, 2, 1], vertical_alignment='center')
    with title_slot:
        st.caption('Freedom Flow / ' + t('Рабочее пространство'))
    with language_slot:
        language_picker()
try:
    store = open_store()
    identity = require_identity(store)
except StorageError:
    st.title(t('Рабочее пространство временно недоступно'))
    st.error(t('Не удалось подключиться к хранилищу аккаунтов. Проверьте доступность MariaDB и настройки подключения.'))
    if st.button(t('Повторить подключение'), type='primary'):
        st.rerun()
    st.caption(t('Существующие аккаунты и заметки сохраняются в выбранном хранилище.'))
    st.stop()
user_id = identity['id']
brand(sidebar=True)
section = st.sidebar.radio(t('Рабочее пространство'), list(NAV), format_func={key: t(value) for key, value in NAV.items()}.get, key='section')
with st.sidebar.expander(t('Источник данных'), icon=':material/database:'):
    prefer_database = database_configured() and (not os.environ.get('MONEYGRAPH_DB'))
    configured_source = os.environ.get('MONEYGRAPH_DATA_SOURCE', '').lower()
    if configured_source in ('files', 'mariadb'):
        prefer_database = configured_source == 'mariadb'
    source = st.selectbox(t('Источник аналитики'), ['Файлы CSV / Parquet', 'MariaDB'], index=int(prefer_database), key='data_source', format_func=translator())
    data_dir = st.text_input(t('Каталог parquet'), args.data, disabled=source == 'MariaDB')
    out_dir = st.text_input(t('Каталог CSV'), args.out, disabled=source == 'MariaDB')
    st.caption(t('Для файлов: пути относительно корня проекта. MariaDB: снимки из настроенного сервера.'))
try:
    with account_slot:
        account_controls(store, identity)
except StorageError:
    st.error(t('Не удалось выполнить действие с аккаунтом. Проверьте подключение и повторите.'))
    st.stop()
st.sidebar.caption(t('Прототип команды · не официальный сервис банка'))

def open_ai(gid):
    st.session_state['ai_selected_gid'] = str(gid)
    st.session_state['ai_mode'] = 'Выбрать участника'
    st.session_state['section'] = 'AI-расследователь'

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

@st.cache_data(show_spinner=False, ttl=60, max_entries=4)
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
        with st.sidebar.expander(t('Снимок MariaDB'), expanded=True):
            snapshot_id = st.selectbox(t('Версия аналитики'), list(by_id), format_func=lambda value: f"{by_id[value]['created_at']} · {value[:8]}")
            if st.button(t('Обновить данные'), icon=':material/refresh:'):
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
    st.info(t('Подключите данные, чтобы начать проверку. Укажите источник в боковой панели «Источник данных».'))
    with st.expander(t('Подробности загрузки и команда расчёта'), expanded=True):
        st.error(t(str(error) if isinstance(error, DatabaseSourceError) else 'Не удалось загрузить или сохранить набор. Проверьте файлы и подключение к хранилищу.'))
        st.code('python run.py --data ./data --out ./out' + (' --mysql' if source == 'MariaDB' else ''), language='bash')
    if st.button(t('Повторить загрузку'), icon=':material/refresh:'):
        available_snapshots.clear()
        database_bundle.clear()
        st.rerun()
    if section != 'Мои проверки':
        st.stop()
if section == 'Обзор':
    metrics([len(nodes), len(edges), int(edges.n_tx.sum()), len(clusters)])
    st.caption(t('Наблюдаемый оборот: {v0} KZT · исходных участников: {v1} · изолированных: {v2}', v0=f'{edges.sum_kzt.sum():,.2f}', v1=f'{nodes.is_seed.sum()}', v2=f'{(nodes.is_seed & nodes.in_deg.eq(0) & nodes.out_deg.eq(0)).sum()}'))
    quick_guide()
    priority_table(top, nodes, dataset_id)
    with st.expander(t('Быстрый переход к узлу'), expanded=True):
        st.subheader(t('С чего начать проверку'), icon=':material/travel_explore:')
        st.write(t('Откройте приоритетный узел, изучите входящие связи и сохраните выводы в проверку.'))
        focus = st.selectbox(t('Приоритетный узел'), top.gid.tolist(), key='focus_gid', format_func=translator())
        st.button(t('Исследовать узел'), type='primary', icon=':material/arrow_forward:', on_click=open_node, args=(focus,))
    with st.expander(t('Исходная таблица и экспорт CSV')):
        st.caption(t('Полный список приоритетных узлов, без фильтров JavaScript-таблицы.'))
        st.dataframe(top, hide_index=True, width='stretch', column_config={'gid': st.column_config.TextColumn('gid'), 'priority_score': st.column_config.ProgressColumn(t('Приоритет проверки'), format='%.3f', min_value=0, max_value=1)})
        st.download_button(t('Скачать полный список CSV'), top.to_csv(index=False).encode('utf-8'), 'top_nodes.csv', 'text/csv')
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader(t('Роли в наблюдаемом графе'))
        st.bar_chart(nodes.role.map({k: t(v) for k, v in ROLE_LABELS.items()}).value_counts().rename(t('Узлы')), color='#19ac99')
    with right, st.container(border=True):
        st.subheader(t('Глубина наблюдения'))
        st.bar_chart(nodes.depth.value_counts().sort_index().rename(t('Узлы')), color='#365d78')
    st.subheader(t('Ограничения выборки'))
    st.write(t('Граф построен по исходящим переводам от 81 исходного участника (seed), до четырёх шагов. Входящие переводы исходных участников неполны; исходящие за границей глубины неизвестны. Все суммы относятся только к наблюдаемой выборке. В транзакциях есть дата, но нет времени суток: совпадение дня не доказывает порядок и путь конкретных денег.'))
elif section == 'Узел и переводы':
    st.session_state.setdefault('node_gid', str(top.iloc[0].gid))
    gid = st.text_input(t('Поиск по полному gid'), key='node_gid', help=t('Скопируйте ID участника из таблицы. gid — его идентификатор в исходной выборке.'), placeholder=t('Введите ID участника')).strip()
    matches = nodes[nodes.gid.eq(gid)]
    if matches.empty:
        st.warning(t('gid не найден. Введите полный целочисленный идентификатор из таблицы.'))
        st.button(t('Открыть первого в списке приоритета'), on_click=open_node, args=(str(top.iloc[0].gid),))
        st.stop()
    row = matches.iloc[0]
    if st.session_state.get('last_view') != (user_id, dataset_id, gid):
        try:
            store.event(user_id, 'Открыт узел', gid)
            st.session_state['last_view'] = (user_id, dataset_id, gid)
        except StorageError:
            st.warning(t('История просмотра временно недоступна. Аналитику можно продолжить.'))
    st.subheader(f'gid {gid}')
    cols = st.columns(3)
    cols[0].metric(t('Роль · гипотеза'), t(ROLE_LABELS.get(row.role, row.role)), help=t(ROLE_HINTS.get(row.role)))
    cols[1].metric(t('Сила правила'), f'{row.role_score:.3f}', help=t('Насколько выражены признаки выбранной роли по нашему правилу. Не вероятность.'))
    cols[2].metric(t('Приоритет проверки'), f'{row.priority_score:.3f}', help=t('Очередность ручной проверки от 0 до 1. Не вероятность нарушения.'))
    incoming = f'{row.in_kzt:,.2f}'.replace(',', ' ')
    outgoing = f'{row.out_kzt:,.2f}'.replace(',', ' ')
    st.write(t('Получил **{v0} ₸** от **{v1}** участников. Отправил **{v2} ₸** **{v3}** получателям.', v0=f'{incoming}', v1=f'{int(row.in_deg)}', v2=f'{outgoing}', v3=f'{int(row.out_deg)}'))
    st.caption(t('Суммы и связи относятся только к наблюдаемой выборке.'))
    for warning in node_warnings(row):
        st.warning(t(warning))
    with st.expander(t('Исходное объяснение из расчёта')):
        st.text(row.evidence)
    parts = contributions(row)
    if not parts.empty:
        with st.expander(t('Почему такой приоритет: вклад каждого признака')):
            parts['Компонента'] = parts['Компонента'].map(t)
            parts = parts.rename(columns=t)
            st.bar_chart(parts.set_index(t('Компонента')))
            st.dataframe(parts, hide_index=True, width='stretch')
            st.caption(t('Вклады суммируются в priority_score. Это объяснение правила, а не причинности или вероятности.'))
    with st.container(border=True):
        st.subheader(t('Кто выше по наблюдаемой цепочке'))
        st.caption(t('Обратный поиск до 4 шагов: кто может достичь выбранного узла по направлению переводов. В таблице один кратчайший путь на кандидата, до 20 кандидатов по приоритету.'))
        depth = st.slider(t('Глубина поиска предшественников'), 1, 4, 4)
        candidates = upstream(nodes, edges, gid, cutoff=depth)
        if candidates.empty:
            st.info(t('В выбранной глубине предшественников не найдено. Это не исключает внешние источники вне выборки.'))
        else:
            st.dataframe(readable(candidates), hide_index=True, width='stretch', column_config=table_columns())
        st.caption(t('Путь в графе не доказывает движение одной суммы или общее управление участниками.'))
        st.download_button(t('Скачать записку для проверки'), case_note(row, candidates), f'case_{gid}.md', 'text/markdown')
    st.caption(t('Кластер {v0} · глубина {v1} · вход {v2} KZT от {v3} · выход {v4} KZT к {v5} · pass_through {v6}', v0=f'{row.cluster_id}', v1=f'{int(row.depth)}', v2=f'{row.in_kzt:,.2f}', v3=f'{int(row.in_deg)}', v4=f'{row.out_kzt:,.2f}', v5=f'{int(row.out_deg)}', v6=f'{row.pass_through:.3f}'))
    if 'fast2' in row:
        with st.expander(t('Как читать признаки потока')):
            st.write(t('Быстрое сопоставление сумм (fast2): {v0}. Входы и выходы сопоставляются за 0–2 календарных дня.', v0=f'{float(row.fast2):.3f}'))
            st.caption(t('В данных есть только даты. Порядок переводов внутри дня и путь конкретных денег не доказаны. Для исходных участников входы неполны.'))
    st.subheader(t('Направленные переводы · один шаг'))
    controls = st.columns(3)
    limit = controls[0].slider(t('Лимит соседей'), 1, 60, 30, key=f'neighbors_{gid}')
    minimum = controls[1].number_input(t('Минимум на пару, KZT'), min_value=0.0, value=0.0, step=5000.0, key=f'minimum_{gid}')
    direction = controls[2].selectbox(t('Направление'), ['both', 'in', 'out'], key=f'direction_{gid}', format_func=lambda x, tr=translator(): tr({'both': 'Оба', 'in': 'Входящие', 'out': 'Исходящие'}[x]))
    selected, available = ego_edges(edges, gid, limit, minimum, direction)
    st.caption(t('Показано {v0} из {v1} соседей после фильтра; {v2} направленных пар. Соседи отобраны по сумме переводов. Стрелка ведёт от плательщика к получателю; сумма — при наведении на середину ребра и в таблице.', v0=f'{min(available, limit)}', v1=f'{available}', v2=f'{len(selected)}'))
    if selected.empty:
        st.info(t('Нет наблюдаемых переводов для выбранных фильтров.'))
    st.caption(t('Крупный узел — выбранный участник. Наведите на узел для роли и приоритета; на середину связи — для суммы.'))
    st.plotly_chart(figure(gid, selected, nodes, cached_layout(gid, selected), translate=t), width='stretch')
    st.dataframe(selected, hide_index=True, width='stretch', column_config=table_columns())
    with st.expander(t('Все наблюдаемые переводы выбранного узла, без фильтра графа')):
        st.dataframe(edges[edges.src.eq(gid) | edges.dst.eq(gid)], hide_index=True, width='stretch', column_config=table_columns())
    try:
        saved = store.case(user_id, dataset_id, gid)
    except StorageError:
        st.error(t('Не удалось прочитать сохранённую заметку. Повторите загрузку перед редактированием.'))
        st.stop()
    with st.expander(t('Сохранить проверку'), expanded=True, icon=':material/bookmark:'):
        with st.form(f'case_{dataset_id}_{gid}'):
            statuses = ['В работе', 'Проверено', 'Отложено']
            status = st.selectbox(t('Статус проверки'), statuses, index=statuses.index(saved['status']) if saved else 0, format_func=translator(), key=f'case_status_{dataset_id}_{gid}')
            note = st.text_area(t('Заметка аналитика'), value=saved['note'] if saved else '', max_chars=10000, key=f'case_note_{dataset_id}_{gid}', placeholder=t('Что наблюдаете и что необходимо проверить дальше?'))
            if st.form_submit_button(t('Сохранить в мои проверки'), type='primary', icon=':material/save:'):
                try:
                    store.save_case(user_id, dataset_id, gid, status, note, case_note(row, candidates))
                except StorageError:
                    st.error(t('Не удалось сохранить проверку. Текст остаётся в форме — повторите попытку.'))
                else:
                    st.success(t('Проверка сохранена. Она будет доступна после перезапуска приложения.'))
    investigation_panel(gid, files, store, user_id, dataset_id)
elif section == 'AI-расследователь':
    st.caption(t('Расследование запускается только по кнопке. Агент проверяет ограниченное окружение участника и ищет подтверждения и противоречия.'))
    mode = st.radio(t('Режим расследования'), ['Очередь цепочек', 'Выбрать участника'], key='ai_mode', horizontal=True, format_func=translator())
    queue = nodes.sort_values(['priority_score', 'gid'], ascending=[False, True]).gid.astype(str).tolist()
    if mode == 'Очередь цепочек':
        investigation_panel(queue[0], files, store, user_id, dataset_id, queue=queue, show_title=False)
    else:
        selected_gid = st.selectbox(t('Выберите участника'), queue, key='ai_selected_gid')
        investigation_panel(selected_gid, files, store, user_id, dataset_id, show_title=False)
elif section == 'Кластеры':
    cluster_id = st.selectbox(t('Кластер'), clusters.cluster_id.tolist(), key='cluster_id', format_func=lambda value, tr=translator(): tr("Кластер {id} · {count} узлов", id=value, count=int(clusters.loc[clusters.cluster_id.eq(value), 'n_nodes'].iloc[0])))
    row = clusters[clusters.cluster_id.eq(cluster_id)].iloc[0]
    cols = st.columns(3)
    cols[0].metric(t('Узлы'), int(row.n_nodes))
    cols[1].metric(t('Исходные участники'), int(row.n_seed))
    cols[2].metric(t('Внутренний оборот, KZT'), f'{row.sum_kzt_internal:,.2f}')
    st.caption(t('Исходный текст данных и выгрузки сохраняются на языке источника.'))
    st.write(row.hypothesis)
    st.caption(t('Кластеры вычислены на ненаправленной проекции с суммой обоих направлений. Принадлежность группе не доказывает общее назначение переводов.'))
    st.write(t('**Участники для начала проверки:**'), row.top_gids)
    members = nodes[nodes.cluster_id.eq(cluster_id)].sort_values(['priority_score', 'gid'], ascending=[False, True])
    st.dataframe(readable(members[['gid', 'role', 'role_score', 'priority_score', 'evidence', 'depth', 'is_seed']]), hide_index=True, width='stretch', column_config=table_columns())
    member = st.selectbox(t('Участник кластера'), members.gid.tolist(), format_func=translator())
    st.button(t('Исследовать участника'), type='primary', on_click=open_node, args=(member,))
    st.button(t('Проверить через AI'), key='cluster_ai', on_click=open_ai, args=(member,))
elif section == 'Мои проверки':
    try:
        records = store.cases(user_id)
    except StorageError:
        st.error(t('Не удалось загрузить проверки. Повторите после восстановления подключения.'))
        st.stop()
    st.caption(t('Личные заметки, статусы и снимки аналитических записок. Каждая проверка связана с версией данных.'))
    if not records:
        st.info(t('Пока нет сохранённых проверок. Откройте узел и нажмите «Сохранить в мои проверки».'))
    if records:
        status_filter = st.selectbox(t('Показать проверки'), ['Все статусы', 'В работе', 'Проверено', 'Отложено'], format_func=translator())
        query = st.text_input(t('Найти в моих проверках'), placeholder=t('ID участника или текст заметки')).strip().lower()
        records = [r for r in records if (status_filter == 'Все статусы' or r['status'] == status_filter) and (not query or query in r['gid'] or query in r['note'].lower())]
        st.caption(t('Найдено проверок: {v0}', v0=f'{len(records)}'))
        if not records:
            st.info(t('Нет проверок с такими условиями. Измените статус или поисковый запрос.'))
    for record in records:
        with st.container(border=True):
            st.subheader(f"gid {record['gid']}")
            st.badge(t(record['status']), color='green' if record['status'] == 'Проверено' else 'gray')
            st.caption(t('Обновлено {v0} UTC · версия {v1}', v0=f"{record['updated_at']}", v1=f"{record['dataset_id'][:12]}"))
            st.text(record['note'] or t('Заметка пока не добавлена.'))
            st.download_button(t('Скачать сохранённую записку'), record['report'], f"case_{record['gid']}.md", 'text/markdown', key=f"download_{record['id']}")
            if record['dataset_id'] == dataset_id:
                st.button(t('Открыть и изменить'), key=f"open_{record['id']}", on_click=open_node, args=(record['gid'],))
                st.button(t('Проверить через AI'), key=f"ai_{record['id']}", on_click=open_ai, args=(record['gid'],))
            else:
                st.caption(t('Архивная версия данных. Сохранённая записка доступна для скачивания.'))
    with st.expander(t('История действий'), icon=':material/history:'):
        try:
            history = store.history(user_id)
            history_frame = pd.DataFrame(history)
            if 'action' in history_frame:
                history_frame['action'] = history_frame['action'].map(t)
            st.dataframe(history_frame, hide_index=True, width='stretch')
        except StorageError:
            st.warning(t('История временно недоступна.'))
else:
    st.subheader(t('Паспорт расчёта'))
    st.caption(t('Источник: {v0} · версия данных: {v1}', v0=t(source), v1=f'{dataset_id[:12]}'))
    with st.container(horizontal=True):
        for name in ('nodes_roles', 'clusters', 'top_nodes'):
            st.download_button(f'{name}.csv', files[f'out/{name}.csv'], f'{name}.csv', 'text/csv')
    if source == 'MariaDB':
        st.success(t('Снимок прочитан из MariaDB. Структура, строки и контрольные суммы проверены при загрузке.'))
        st.code(snapshot_id, language=None)
        st.caption(t('Паспорт времени и диагностики локального запуска не входит в снимок MariaDB. Для него выберите соответствующие локальные файлы.'))
        st.stop()
    st.caption(t('Связывает показанные CSV с конкретным запуском. Хеши проверяют совпадение файлов, а не достоверность исходных сведений.'))
    try:
        report = read_report(out_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        st.warning(t('Паспорт недоступен или не соответствует CSV: {v0}', v0=f'{error}'))
        st.code('python run.py --data ./data --out ./out')
    else:
        st.success(t('Хеши трёх CSV совпадают с паспортом запуска.'))
        st.write(t('Версия алгоритма:'), report['algorithm'])
        diagnostics = report['diagnostics']
        st.write(t('Совпадение топ-20 с простым рейтингом по максимальному входу/выходу: {v0} из 20.', v0=f"{diagnostics['volume_baseline_top20_overlap']}"))
        st.caption(t('Различие с рейтингом по сумме показывает влияние структуры и временных признаков. Оно не доказывает более высокую точность.'))
        sensitivity = pd.DataFrame(list(diagnostics['leave_one_component_out_top20_overlap'].items()), columns=[t('Исключённый компонент'), t('Сохранились в топ-20')])
        st.dataframe(sensitivity, hide_index=True, width='stretch')
        st.caption(t('Чувствительность: исключаем один вклад, остальные веса не меняем. Чем меньше совпадение, тем сильнее рейтинг зависит от компонента.'))
        with st.expander(t('Параметры, версии и контрольные суммы')):
            st.json(report)
