from pathlib import Path

import numpy as np
import pandas as pd


def validate(edges, nodes, tx):
    for frame, cols in [(nodes, ['gid', 'depth', 'is_seed']),
                        (edges, ['src', 'dst', 'sum_kzt', 'n_tx', 'depth']),
                        (tx, ['src', 'dst', 'sum_kzt', 'date'])]:
        if not set(cols) <= set(frame) or frame[cols].isna().any().any():
            raise ValueError('Missing columns or values')
        for col in set(cols) & {'gid', 'src', 'dst'}:
            if not pd.api.types.is_integer_dtype(frame[col]):
                raise ValueError('gid/src/dst must be integers, never float')
    if nodes.gid.duplicated().any() or edges.duplicated(['src', 'dst']).any():
        raise ValueError('Duplicate nodes or edges')
    gids = set(nodes.gid)
    for frame in [edges, tx]:
        if not (set(frame.src) | set(frame.dst)) <= gids:
            raise ValueError('Unknown edge endpoint')
        if not np.isfinite(frame.sum_kzt).all() or (frame.sum_kzt < 0).any():
            raise ValueError('Invalid amounts')
    if not pd.api.types.is_bool_dtype(nodes.is_seed):
        raise ValueError('is_seed must be boolean')
    if not nodes.depth.isin(range(5)).all() or not edges.depth.isin(range(1, 5)).all():
        raise ValueError('Invalid depth')
    if not (nodes.is_seed == nodes.depth.eq(0)).all():
        raise ValueError('Seed/depth disagreement')
    if not pd.api.types.is_integer_dtype(edges.n_tx) or (edges.n_tx <= 0).any():
        raise ValueError('Invalid transaction count')
    if not tx.date.between('2026-07-01', '2026-07-31').all() or not tx.date.eq(tx.date.dt.normalize()).all():
        raise ValueError('Dates must be days in July 2026')
    agg = tx.groupby(['src', 'dst']).agg(amount=('sum_kzt', 'sum'), count=('sum_kzt', 'size'))
    merged = edges.merge(agg, on=['src', 'dst'], how='outer', indicator=True)
    if not merged._merge.eq('both').all() or not np.isclose(merged.sum_kzt, merged.amount, atol=.01, rtol=0).all() or not merged.n_tx.eq(merged['count']).all():
        raise ValueError('Edges/transactions mismatch')


def load(directory):
    directory = Path(directory)
    edges, nodes, tx = [pd.read_parquet(directory / f'{name}.parquet') for name in ['edges', 'nodes', 'transactions']]
    tx['date'] = pd.to_datetime(tx.date, errors='raise')
    validate(edges, nodes, tx)
    return (edges.sort_values(['src', 'dst']).reset_index(drop=True),
            nodes.sort_values('gid').reset_index(drop=True),
            tx.sort_values(['date', 'src', 'dst', 'sum_kzt']).reset_index(drop=True))
