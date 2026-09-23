"""Presentation layer: local styles and a JavaScript investigation table."""
from html import escape
from base64 import b64encode
from pathlib import Path

import streamlit as st
import streamlit.components.v2 as components

ASSETS = Path(__file__).parent / 'frontend'
TITLES = {
    'Обзор': 'Мониторинг транзакций',
    'Узел и переводы': 'Узел и переводы',
    'Кластеры': 'Кластеры связей',
    'Мои проверки': 'Мои проверки',
    'Проверяемость': 'Проверяемость расчёта',
}
DESCRIPTIONS = {
    'Обзор': 'Выберите, с кого начать: приоритет, связи и наблюдаемые суммы в одном месте.',
    'Узел и переводы': 'Проследите переводы, проверьте ограничения и сохраните свой вывод.',
    'Кластеры': 'Исследуйте группы связанных участников и откройте нужного участника.',
    'Мои проверки': 'Продолжите начатую работу: здесь ваши заметки, статусы и записки.',
    'Проверяемость': 'Проверьте источник данных, результаты расчёта и скачайте выгрузки.',
}
ROLE_LABELS = {'consolidator': 'Сборщик средств', 'transit': 'Транзитный участник',
               'distributor': 'Распределитель', 'terminal': 'Конечный получатель',
               'coordinator': 'Координатор связей', 'peripheral': 'Периферийный участник'}
ROLE_HINTS = {
    'consolidator': 'Собирает наблюдаемые переводы от нескольких участников.',
    'transit': 'Получает и отправляет средства дальше в наблюдаемом графе.',
    'distributor': 'Отправляет переводы нескольким получателям.',
    'terminal': 'Нет наблюдаемого продолжения потока внутри доступной глубины.',
    'coordinator': 'Связывает несколько частей наблюдаемой сети.',
    'peripheral': 'Недостаточно признаков для более определённой роли.',
}

NAV = {'Обзор': ':material/grid_view: Обзор', 'Узел и переводы': ':material/tune: Узел и переводы',
       'Кластеры': ':material/hub: Кластеры', 'Мои проверки': ':material/task_alt: Мои проверки',
       'Проверяемость': ':material/description: Проверяемость'}


def table_component():
    return components.component(
        'moneygraph_priority_table',
        html=(ASSETS / 'table.html').read_text(encoding='utf-8'),
        css=(ASSETS / 'table.css').read_text(encoding='utf-8'),
        js=(ASSETS / 'table.js').read_text(encoding='utf-8'),
    )


def apply_theme():
    st.html('<style>' + (ASSETS / 'shell.css').read_text(encoding='utf-8') + '</style>')


def brand_html():
    logo = b64encode((ASSETS / 'freedom-logo.png').read_bytes()).decode('ascii')
    return ('<div class="mg-brand"><span class="mg-logo">'
            f'<img src="data:image/png;base64,{logo}" alt="Freedom"></span>'
            '<div><strong>Freedom <em>Flow</em></strong><small>АНАЛИТИКА ДЕНЕЖНЫХ ПОТОКОВ</small></div></div>')


def brand(sidebar=False):
    target = st.sidebar if sidebar else st
    target.html(brand_html())


def page_header(section, identity, storage_label="Локальное пространство"):
    title = TITLES[section]
    initials = ''.join(word[0] for word in identity['name'].split()[:2]).upper()
    st.html(f'<div class="mg-topbar"><div>Freedom Flow <span>/</span> '
            f'<strong>{escape(title)}</strong></div><div class="mg-profile">'
            f'<span class="mg-local"><i></i> {escape(storage_label)}</span>'
            f'<span class="mg-avatar" title="{escape(identity["name"], quote=True)}">'
            f'{escape(initials)}</span></div></div>')
    st.html(f'<div class="mg-heading"><div><h1>{escape(title)}</h1>'
            f'<p>{DESCRIPTIONS[section]}</p></div>'
            '<span class="mg-period">▦ &nbsp; Июль 2026 <small>Период выборки</small></span></div>')


def metrics(values=None):
    labels = ['Участники', 'Связи', 'Переводы', 'Группы']
    notes = ['Участники наблюдаемого графа', 'Связи между участниками',
             'В исходной выборке', 'Группы связанных узлов']
    values = values if values is not None else [None] * 4
    with st.container(key='summary_metrics'):
        for i, col in enumerate(st.columns(4)):
            with col.container(border=True):
                st.metric(labels[i], '—' if values[i] is None else f'{values[i]:,}'.replace(',', ' '))
                st.caption(notes[i] if values[i] is not None else 'Данные ещё не загружены')


def priority_table(top=None, nodes=None, dataset_id='empty'):
    rows = []
    if top is not None and nodes is not None:
        indexed = nodes.set_index('gid')
        for row in top.itertuples():
            node = indexed.loc[row.gid]
            rows.append(dict(gid=str(row.gid), rank=int(row.rank), role=str(row.role),
                             score=float(row.priority_score), amount=float(node.in_kzt),
                             outgoing=float(node.out_kzt), why=str(row.why)))
    key = 'priority_table'

    def open_selected():
        value = st.session_state.get(key)
        gid = value.open_gid if value else None
        # A client event is navigation only, never a source of account identity.
        if isinstance(gid, str) and gid in {row['gid'] for row in rows}:
            st.session_state['node_gid'] = gid
            st.session_state['section'] = 'Узел и переводы'

    table_component()(data={'rows': rows, 'dataset': dataset_id}, key=key,
                      on_open_gid_change=open_selected)


def table_columns():
    return {
        'gid': st.column_config.TextColumn('ID участника', help='gid — идентификатор участника в исходном наборе'),
        'src': st.column_config.TextColumn('Отправитель'),
        'dst': st.column_config.TextColumn('Получатель'),
        'sum_kzt': st.column_config.NumberColumn('Сумма, ₸', format='localized'),
        'n_tx': st.column_config.NumberColumn('Переводов'),
        'role': st.column_config.TextColumn('Роль · гипотеза'),
        'priority_score': st.column_config.ProgressColumn('Приоритет проверки', min_value=0, max_value=1, format='%.3f'),
        'role_score': st.column_config.NumberColumn('Сила правила', format='%.3f'),
        'depth': st.column_config.NumberColumn('Шаг от исходных участников'),
        'is_seed': st.column_config.CheckboxColumn('Исходный участник'),
        'evidence': st.column_config.TextColumn('Наблюдения'),
        'why': st.column_config.TextColumn('Почему в списке'),
        'hops': st.column_config.NumberColumn('Шагов до участника'),
        'path': st.column_config.TextColumn('Путь по направлению переводов'),
        'rank': st.column_config.NumberColumn('Место'),
    }


def readable(frame):
    result = frame.copy()
    if 'role' in result:
        result['role'] = result.role.map(ROLE_LABELS).fillna(result.role)
    return result


def quick_guide():
    st.html('<div class="mg-guide"><div><span>01</span><b>Выберите участника</b><p>Начните с верхних строк списка.</p></div>'
            '<div><span>02</span><b>Исследуйте связи</b><p>Сопоставьте роль, суммы и ограничения.</p></div>'
            '<div><span>03</span><b>Сохраните проверку</b><p>Добавьте заметку и следующий шаг.</p></div></div>')
