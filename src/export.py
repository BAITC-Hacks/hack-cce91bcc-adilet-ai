from pathlib import Path

import numpy as np
import pandas as pd

from .roles import ORDER

REQUIRED = 'gid role role_score cluster_id priority_score evidence in_deg out_deg in_kzt out_kzt pagerank pass_through depth is_seed truncated_by_depth'.split()


def outputs(df, edges):
    df = df.copy()
    df['evidence'] = [f'in={r.in_deg};out={r.out_deg};inKZT={r.in_kzt:.2f};outKZT={r.out_kzt:.2f};depth={r.depth};sources={r.seed_sources};fast2={r.fast2:.3f};seed={int(r.is_seed)};cut={int(r.truncated_by_depth)}' for r in df.itertuples()]
    df = df[REQUIRED + [col for col in df if col not in REQUIRED]].sort_values('gid')
    ranked = df.sort_values(['priority_score', 'gid'], ascending=[False, True])
    top = ranked.head(20)[['gid', 'role', 'priority_score', 'evidence']].rename(columns={'evidence': 'why'})
    top.insert(0, 'rank', range(1, len(top)+1))
    mapping = df.set_index('gid').cluster_id
    internal = edges.assign(cluster_id=edges.src.map(mapping), dst_cluster=edges.dst.map(mapping))
    totals = internal.loc[internal.cluster_id.eq(internal.dst_cluster)].groupby('cluster_id').sum_kzt.sum()
    records = []
    for cid, part in ranked.groupby('cluster_id', sort=True):
        total = float(totals.get(cid, 0.))
        records.append(dict(cluster_id=cid, n_nodes=len(part), n_seed=int(part.is_seed.sum()), sum_kzt_internal=total,
                            top_gids=';'.join(map(str, part.gid.head(5))),
                            hypothesis=f'Наблюдаемая группа: {len(part)} узлов, {int(part.is_seed.sum())} seed, внутренний оборот {total:.2f} KZT; depth4={int(part.truncated_by_depth.sum())}. Исходящая выборка до 4 колен; назначение требует проверки.'))
    return df, pd.DataFrame(records), top


def validate_outputs(nodes, clusters, top):
    for frame in (nodes, clusters, top):
        if frame.isna().any().any() or not np.isfinite(frame.select_dtypes(include='number')).all().all():
            raise ValueError('Nonfinite/missing export values')
        if frame.astype(str).apply(lambda s: s.str.strip().str.lower().isin(['', 'nan', 'inf', '-inf']).any()).any():
            raise ValueError('Empty export text')
    if not set(REQUIRED) <= set(nodes) or nodes.gid.duplicated().any() or not pd.api.types.is_integer_dtype(nodes.gid):
        raise ValueError('Invalid node contract')
    if not nodes.role.isin(ORDER+['peripheral']).all() or not nodes[['role_score', 'priority_score']].ge(0).all().all() or not nodes[['role_score', 'priority_score']].le(1).all().all():
        raise ValueError('Invalid roles/scores')
    if not nodes.evidence.str.len().between(1, 200).all() or not nodes.evidence.str.contains(r'\d').all():
        raise ValueError('Invalid evidence')
    if nodes.loc[nodes.truncated_by_depth, 'role'].eq('terminal').any():
        raise ValueError('Depth boundary classified as terminal')
    if clusters.cluster_id.duplicated().any() or clusters.set_index('cluster_id').n_nodes.to_dict() != nodes.groupby('cluster_id').size().to_dict():
        raise ValueError('Cluster membership mismatch')
    expected = nodes.sort_values(['priority_score', 'gid'], ascending=[False, True]).head(len(top))
    if len(top) < min(20, len(nodes)) or top.gid.tolist() != expected.gid.tolist() or top['rank'].tolist() != list(range(1, len(top)+1)):
        raise ValueError('Invalid top ranking')
    if top.role.tolist() != expected.role.tolist() or not np.array_equal(top.priority_score, expected.priority_score):
        raise ValueError('Top values mismatch')


def export(df, edges, directory):
    frames = outputs(df, edges)
    validate_outputs(*frames)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, frame in zip(['nodes_roles', 'clusters', 'top_nodes'], frames):
        frame.to_csv(directory / f'{name}.csv', index=False, float_format='%.17g')
    return frames
