"""Presentation layer: local styles and a JavaScript investigation table."""
from html import escape
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


def brand(sidebar=False):
    target = st.sidebar if sidebar else st
    target.html('<div class="mg-brand"><span class="mg-logo" aria-hidden="true">◈</span>'
                '<div><strong>MoneyGraph</strong><small>ANALYTICS / WORKSPACE</small></div></div>')


def page_header(section, identity):
    title = TITLES[section]
    initials = ''.join(word[0] for word in identity['name'].split()[:2]).upper()
    st.html(f'<div class="mg-topbar"><div>Рабочее пространство <span>/</span> '
            f'<strong>{escape(title)}</strong></div><div class="mg-profile">'
            f'<span class="mg-local"><i></i> Локальное пространство</span>'
            f'<span class="mg-avatar" title="{escape(identity["name"], quote=True)}">'
            f'{escape(initials)}</span></div></div>')
    st.html(f'<div class="mg-heading"><div><h1>{escape(title)}</h1>'
            '<p>Обзор переводов и связей для внимательной проверки</p></div>'
            '<span class="mg-period">▦ &nbsp; Июль 2026 <small>Период выборки</small></span></div>')


def metrics(values=None):
    labels = ['Узлы', 'Направленные пары', 'Переводы', 'Кластеры']
    notes = ['Участники наблюдаемого графа', 'Связи между участниками',
             'В исходной выборке', 'Группы связанных узлов']
    values = values if values is not None else [None] * 4
    with st.container(key='summary_metrics'):
        for start in (0, 2):
            for i, col in enumerate(st.columns(2), start=start):
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
