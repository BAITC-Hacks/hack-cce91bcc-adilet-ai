import networkx as nx
import numpy as np


def build_graph(edges, nodes):
    graph = nx.DiGraph()
    graph.add_nodes_from(sorted(nodes.gid))
    for row in edges.sort_values(['src', 'dst']).itertuples(index=False):
        # Zero-volume edges get a finite large distance.
        graph.add_edge(row.src, row.dst, sum_kzt=float(row.sum_kzt),
                       n_tx=int(row.n_tx), distance=1 / max(np.log1p(row.sum_kzt), 1e-12))
    return graph


def centrality(graph, df):
    df['pagerank'] = df.gid.map(nx.pagerank(graph, weight='sum_kzt', tol=1e-12, max_iter=1000))
    df['betweenness'] = df.gid.map(nx.betweenness_centrality(graph, k=min(128, len(graph)), seed=42, weight='distance'))
    sizes = {gid: len(component) for component in nx.strongly_connected_components(graph) for gid in component}
    df['scc_size'] = df.gid.map(sizes)
    sources = dict.fromkeys(graph, 0)
    for seed in df.loc[df.is_seed, 'gid']:
        for gid in nx.single_source_shortest_path_length(graph, seed, cutoff=4):
            if gid != seed:
                sources[gid] += 1
    df['seed_sources'] = df.gid.map(sources)
    return df
