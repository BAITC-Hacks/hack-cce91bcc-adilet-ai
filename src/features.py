import numpy as np


def basic_features(graph, nodes):
    df = nodes.copy()
    for prefix, degree in [('in', graph.in_degree), ('out', graph.out_degree)]:
        for suffix, weight in [('deg', None), ('kzt', 'sum_kzt'), ('tx', 'n_tx')]:
            df[f'{prefix}_{suffix}'] = df.gid.map(dict(degree(weight=weight)))
    df['missing_inflow'] = df.in_kzt.eq(0)
    df['pass_through'] = np.divide(df.out_kzt, df.in_kzt, out=np.zeros(len(df)), where=df.in_kzt.gt(0))
    df['truncated_by_depth'] = df.depth.eq(4) & df.out_deg.eq(0)
    return df
