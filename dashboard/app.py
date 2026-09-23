"""Local read-only MoneyGraph dashboard."""
import argparse
from pathlib import Path
import sys
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.data import load_bundle, signature, node_warnings
from dashboard.graph import ego_edges, layout, figure

parser = argparse.ArgumentParser()
parser.add_argument('--data', default='./data')
parser.add_argument('--out', default='./out')
args, _ = parser.parse_known_args()
st.set_page_config(page_title='MoneyGraph · Граф денег', page_icon='↗', layout='wide')
st.title('MoneyGraph')
st.caption('Наблюдаемые переводы · июль 2026 · аналитические гипотезы')
data_dir = st.sidebar.text_input('Каталог parquet', args.data)
out_dir = st.sidebar.text_input('Каталог CSV', args.out)
st.sidebar.caption('Относительные пути считаются от корня проекта.')

@st.cache_data(show_spinner=False)
def cached_bundle(data_dir, out_dir, version):
    return load_bundle(data_dir, out_dir)

@st.cache_data(show_spinner=False)
def cached_layout(gid, edges):
    return layout(gid, edges)

try:
    nodes, clusters, top, edges = cached_bundle(data_dir, out_dir, signature(data_dir, out_dir))
except (OSError, ValueError, KeyError, TypeError) as error:
    st.error(f'Не удалось загрузить данные: {error}')
    st.info('Проверьте пути и сформируйте три CSV из исходного датасета:')
    st.code('python run.py --data ./data --out ./out', language='bash')
    st.stop()

st.info('Роль — аналитическая гипотеза. Сила правила (role_score) и приоритет проверки (priority_score) — разные показатели; оба не являются вероятностью правонарушения.')
section = st.sidebar.radio('Раздел', ['Обзор', 'Узел и переводы', 'Кластеры'])
if section == 'Обзор':
    columns = st.columns(4)
    for col, label, value in zip(columns, ['Узлы', 'Направленные пары', 'Переводы', 'Кластеры'], [len(nodes), len(edges), int(edges.n_tx.sum()), len(clusters)]):
        col.metric(label, f'{value:,}'.replace(',', ' '))
    st.caption(f'Наблюдаемый оборот: {edges.sum_kzt.sum():,.2f} KZT · seed: {nodes.is_seed.sum()} · изолированные seed: {(nodes.is_seed & nodes.in_deg.eq(0) & nodes.out_deg.eq(0)).sum()}')
    st.subheader('Приоритет проверки')
    st.caption('Сортировка — нажатием на заголовок. Скопируйте полный gid для поиска в разделе «Узел и переводы».')
    st.dataframe(top, hide_index=True, width='stretch', column_config={'gid':st.column_config.TextColumn('gid'), 'priority_score':st.column_config.NumberColumn('Приоритет проверки',format='%.3f')})
    st.download_button('Скачать показанную таблицу', top.to_csv(index=False).encode('utf-8'), 'top_nodes.csv', 'text/csv')
    st.subheader('Ограничения выборки')
    st.write('Граф построен по исходящим переводам от seed до 4 колен. Входящие seed неполны; исходящие за границей глубины неизвестны. Все суммы относятся только к наблюдаемой выборке. В транзакциях есть дата, но нет времени суток: совпадение дня не доказывает порядок и путь конкретных денег.')
elif section == 'Узел и переводы':
    gid = st.text_input('Поиск по полному gid', value=str(top.iloc[0].gid)).strip()
    matches = nodes[nodes.gid.eq(gid)]
    if matches.empty:
        st.warning('gid не найден. Введите полный целочисленный идентификатор из таблицы.')
        st.stop()
    row = matches.iloc[0]
    st.subheader(f'gid {gid}')
    cols = st.columns(3)
    cols[0].metric('Роль · гипотеза', row.role)
    cols[1].metric('Сила правила', f'{row.role_score:.3f}')
    cols[2].metric('Приоритет проверки', f'{row.priority_score:.3f}')
    st.write('**Наблюдения:**', row.evidence)
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
else:
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
