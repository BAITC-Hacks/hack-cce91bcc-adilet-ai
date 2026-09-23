import numpy as np
import pandas as pd

ORDER = ['coordinator', 'transit', 'consolidator', 'distributor', 'terminal']


def scale(values):
    values = np.asarray(values, dtype=float)
    positive = values[values > 0]
    return np.clip(np.log1p(values) / np.log1p(np.quantile(positive, .99, method='linear')), 0, 1) if len(positive) else np.zeros_like(values)


def assign_roles(df):
    L = scale
    both = df.in_deg.gt(0) & df.out_deg.gt(0)
    denom = (df.in_deg + df.out_deg).clip(lower=1)
    i = ((df.in_deg - df.out_deg) / denom).clip(lower=0)
    o = ((df.out_deg - df.in_deg) / denom).clip(lower=0)
    b = np.minimum(df.in_kzt, df.out_kzt) / np.maximum(df.in_kzt, df.out_kzt).replace(0, 1)
    positive = df.loc[df.betweenness > 0, 'betweenness']
    threshold = positive.quantile(.9) if len(positive) else float('inf')
    scores = pd.DataFrame(index=df.index)
    scores['consolidator'] = (.40*L(df.in_deg)+.25*L(df.in_tx)+.20*L(df.in_kzt)+.15*i).where(~df.is_seed & df.in_deg.ge(3) & df.out_deg.gt(0), 0)
    scores['transit'] = (.35*b+.30*df.fast2+.20*L(np.minimum(df.in_tx, df.out_tx))+.15*L(np.minimum(df.in_deg, df.out_deg))).where(~df.is_seed & both, 0)
    scores['distributor'] = (.40*L(df.out_deg)+.25*L(df.out_tx)+.20*L(df.out_kzt)+.15*o).where(df.out_deg.ge(3), 0)
    scores['terminal'] = pd.Series(.55*L(df.in_kzt)+.25*L(df.in_deg)+.20*L(df.in_tx), index=df.index).where(~df.is_seed & df.depth.lt(4) & df.in_deg.gt(0) & df.out_deg.eq(0), 0)
    eligible = both & ((df.betweenness.gt(0) & df.betweenness.ge(threshold)) | df.seed_sources.ge(2) | df.cross_nbrs.ge(2))
    scores['coordinator'] = pd.Series(.45*L(df.betweenness)+.25*L((df.seed_sources-1).clip(lower=0))+.20*L(df.cross_nbrs)+.10*L(df.pagerank), index=df.index).where(eligible, 0)
    scores = scores[ORDER]
    strength = scores.max(axis=1)
    df['role'] = scores.idxmax(axis=1).where(strength.ge(.55), 'peripheral')
    df['role_score'] = strength.where(strength.ge(.55), 1-strength)
    uncertain = df.truncated_by_depth | (df.is_seed & (df.in_deg+df.out_deg).eq(0))
    df.loc[uncertain & df.role.eq('peripheral'), 'role_score'] = df.loc[uncertain & df.role.eq('peripheral'), 'role_score'].clip(upper=.35)
    for role in ORDER:
        df[f'score_{role}'] = scores[role]
    central = .5*L(df.pagerank)+.5*L(df.betweenness)
    bridge = .7*L(df.betweenness)+.3*df.scc_size.gt(1)
    parts = {
        'centrality': .25*central,
        'volume': .20*L(np.maximum(df.in_kzt, df.out_kzt)),
        'structure': .15*scores.drop(columns='terminal').max(axis=1),
        'multisource': .15*L((df.seed_sources-1).clip(lower=0)),
        'temporal': .10*df.fast2.where(~df.is_seed, 0),
        'bridge': .10*bridge,
        'strength': .05*strength,
    }
    for name, values in parts.items():
        df[f'priority_part_{name}'] = values
    df['priority_score'] = (.25*central+.20*L(np.maximum(df.in_kzt, df.out_kzt))+.15*scores.drop(columns='terminal').max(axis=1)
                            +.15*L((df.seed_sources-1).clip(lower=0))+.10*df.fast2.where(~df.is_seed, 0)+.10*bridge+.05*strength)
    return df
