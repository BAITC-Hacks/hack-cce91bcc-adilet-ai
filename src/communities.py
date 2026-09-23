import networkx as nx


def communities(graph, df):
    undirected = nx.Graph()
    undirected.add_nodes_from(graph)
    for src, dst, attrs in graph.edges(data=True):
        previous = undirected.get_edge_data(src, dst, {}).get('weight', 0.)
        undirected.add_edge(src, dst, weight=previous + attrs['sum_kzt'])
    if undirected.size(weight='weight') > 0:
        parts = nx.community.louvain_communities(undirected, weight='weight', seed=42)
    else:
        parts = [{gid} for gid in graph]
    mapping = {gid: i for i, part in enumerate(sorted(parts, key=min)) for gid in part}
    df['cluster_id'] = df.gid.map(mapping)
    df['cross_nbrs'] = df.gid.map({gid: sum(mapping[nbr] != mapping[gid] for nbr in set(graph.predecessors(gid)) | set(graph.successors(gid))) for gid in graph})
    return df
