"""Bounded directed one-hop view with deterministic layout."""
import networkx as nx


def ego_edges(edges, gid, limit=30, minimum=0, direction='both'):
    if not 1 <= limit <= 60:
        raise ValueError('Лимит соседей должен быть от 1 до 60')
    mask = edges.src.eq(gid) | edges.dst.eq(gid)
    if direction == 'in':
        mask = edges.dst.eq(gid)
    elif direction == 'out':
        mask = edges.src.eq(gid)
    selected = edges[mask & edges.sum_kzt.ge(minimum)].copy()
    selected['neighbor'] = selected.dst.where(selected.src.eq(gid), selected.src)
    amounts = selected.groupby('neighbor').sum_kzt.sum()
    neighbors = sorted(amounts.index, key=lambda value: (-amounts[value], int(value)))[:limit]
    selected = selected[selected.neighbor.isin(neighbors)].drop(columns='neighbor')
    return selected.sort_values(['sum_kzt', 'src', 'dst'], ascending=[False, True, True]).reset_index(drop=True), len(amounts)


def layout(gid, edges):
    graph = nx.DiGraph()
    graph.add_node(gid)
    graph.add_edges_from(edges[['src', 'dst']].itertuples(index=False, name=None))
    positions = nx.spring_layout(graph, seed=42, iterations=50)
    return {node: (float(point[0]), float(point[1])) for node, point in positions.items()}


def figure(gid, edges, nodes, positions):
    import plotly.graph_objects as go
    fig = go.Figure()
    pairs = set(edges[['src', 'dst']].itertuples(index=False, name=None))
    for row in edges.itertuples():
        x0, y0 = positions[row.src]
        x1, y1 = positions[row.dst]
        if (row.dst, row.src) in pairs and row.src != row.dst:
            dx, dy = x1 - x0, y1 - y0
            length = max((dx*dx + dy*dy)**.5, 1e-9)
            ox, oy = -.025 * dy / length, .025 * dx / length
            x0, y0, x1, y1 = x0+ox, y0+oy, x1+ox, y1+oy
        # Offset the arrow endpoints so that direction remains visible outside node markers.
        fig.add_annotation(x=x0 + .88*(x1-x0), y=y0 + .88*(y1-y0), ax=x0 + .12*(x1-x0), ay=y0 + .12*(y1-y0), xref='x', yref='y', axref='x', ayref='y', showarrow=True, arrowhead=3, arrowsize=1.4, arrowwidth=1.4, arrowcolor='#8a9ba8', text='')
        fig.add_trace(go.Scatter(x=[(x0+x1)/2], y=[(y0+y1)/2], mode='markers', marker=dict(size=10, color='rgba(138,155,168,0.35)'), text=[f'{row.src} → {row.dst}<br>{row.sum_kzt:,.2f} KZT · {row.n_tx} переводов'], hovertemplate='%{text}<extra></extra>', showlegend=False))
    indexed = nodes.set_index('gid')
    gids = list(positions)
    labels = {'consolidator':'Сборщик средств', 'transit':'Транзитный участник', 'distributor':'Распределитель', 'terminal':'Конечный получатель', 'coordinator':'Координатор связей', 'peripheral':'Периферийный участник'}
    colors = {'consolidator':'#6366f1', 'transit':'#06b6d4', 'distributor':'#f59e0b', 'terminal':'#ec4899', 'coordinator':'#22c55e', 'peripheral':'#94a3b8'}
    fig.add_trace(go.Scatter(x=[positions[g][0] for g in gids], y=[positions[g][1] for g in gids], mode='markers', text=[f'gid {g}<br>{labels[indexed.loc[g, "role"]]}<br>Приоритет {indexed.loc[g, "priority_score"]:.3f}' for g in gids], hovertemplate='%{text}<extra></extra>', marker=dict(size=[22 if g == gid else 13 for g in gids], color=[colors[indexed.loc[g,'role']] for g in gids], line=dict(width=2,color='white')), showlegend=False))
    fig.update_layout(height=540, margin=dict(l=10,r=10,t=10,b=10), xaxis=dict(visible=False), yaxis=dict(visible=False,scaleanchor='x'), hovermode='closest', plot_bgcolor='rgba(0,0,0,0)')
    return fig
