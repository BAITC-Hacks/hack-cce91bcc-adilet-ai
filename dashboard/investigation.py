"""Read-only investigation helpers; no role inference in the presentation layer."""
import networkx as nx
import pandas as pd

COMPONENTS = {'centrality':'Центральность','volume':'Объём','structure':'Структура',
              'multisource':'Несколько seed','temporal':'Временное окно','bridge':'Посредничество и SCC','strength':'Сила роли'}


def upstream(nodes, edges, gid, cutoff=4, limit=20):
    if not 1 <= cutoff <= 4 or not 1 <= limit <= 100:
        raise ValueError('Bounded search only: depth 1–4, limit 1–100')
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes.gid)
    graph.add_edges_from(sorted(edges[['src','dst']].itertuples(index=False,name=None)))
    paths = nx.single_source_shortest_path(graph.reverse(copy=False), gid, cutoff=cutoff)
    candidates = nodes[nodes.gid.isin(set(paths)-{gid})].copy()
    candidates['hops'] = candidates.gid.map(lambda value:len(paths[value])-1)
    candidates['path'] = candidates.gid.map(lambda value:' → '.join(reversed(paths[value])))
    return candidates.sort_values(['priority_score','gid'],ascending=[False,True]).head(limit)[['gid','role','priority_score','hops','path']]


def contributions(row):
    records = [{'Компонента':label,'Вклад в приоритет':float(row[f'priority_part_{name}'])}
               for name,label in COMPONENTS.items() if f'priority_part_{name}' in row]
    return pd.DataFrame(records,columns=['Компонента','Вклад в приоритет'])


def case_note(row, candidates):
    lines = [f'# Наблюдения по gid {row.gid}', '', f'Роль-гипотеза: {row.role}',
             f'Сила правила: {row.role_score:.6f}; приоритет проверки: {row.priority_score:.6f}',
             '', str(row.evidence), '', '## Предшественники в наблюдаемом графе']
    for candidate in candidates.itertuples():
        lines.append(f'- {candidate.gid}: {candidate.role}; приоритет {candidate.priority_score:.6f}; путь {candidate.path}')
    if candidates.empty:
        lines.append('Наблюдаемых предшественников в выбранной глубине нет.')
    lines += ['', '## Ограничения', 'Роли и связи не доказывают общее управление или правонарушение. '
              'Путь означает только последовательность направленных рёбер, не движение конкретной суммы. '
              'Даты без времени суток; входы seed неполны; выборка обрезана на глубине 4.']
    return '\n'.join(lines)
