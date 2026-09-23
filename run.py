import argparse
import json
from time import perf_counter


def main():
    started = perf_counter()
    from src.load import load
    from src.graph import build_graph, centrality
    from src.features import basic_features
    from src.temporal import temporal_features
    from src.communities import communities
    from src.roles import assign_roles
    from src.export import export
    parser = argparse.ArgumentParser(description='MoneyGraph: observed graph, deterministic v1 rules')
    parser.add_argument('--data', default='./data')
    parser.add_argument('--out', default='./out')
    args = parser.parse_args()
    times = {'imports': perf_counter()-started}
    def stage(name, fn, *values):
        start = perf_counter()
        result = fn(*values)
        times[name] = perf_counter()-start
        return result
    edges, nodes, tx = stage('load', load, args.data)
    graph = stage('graph', build_graph, edges, nodes)
    df = stage('features', basic_features, graph, nodes)
    df = stage('temporal', temporal_features, df, tx)
    df = stage('centrality', centrality, graph, df)
    df = stage('communities', communities, graph, df)
    df = stage('roles_priority', assign_roles, df)
    frames = stage('export', export, df, edges, args.out)
    times['total'] = perf_counter()-started
    print(json.dumps({'rows': dict(zip(['nodes_roles', 'clusters', 'top_nodes'], map(len, frames))),
                      'edges': len(edges), 'transactions': len(tx), 'sum_kzt': float(edges.sum_kzt.sum()),
                      'roles': df.role.value_counts().to_dict(), 'seconds': times}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
