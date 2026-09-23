from pathlib import Path
import shutil
import numpy as np
import pandas as pd
import pytest
from dashboard.data import ROOT, load_bundle, node_warnings
from dashboard.graph import ego_edges, layout


@pytest.fixture(scope='module')
def real():
    if not (ROOT / 'out/nodes_roles.csv').exists():
        pytest.skip('Нужны реальные CSV: python run.py --data ./data --out ./out')
    return load_bundle('data', 'out')


def test_real_contract_and_identifiers(real):
    nodes, clusters, top, edges = real
    assert len(nodes) == 2248 and len(edges) == 3119
    assert edges.n_tx.sum() == 4840
    assert all(isinstance(gid, str) for gid in nodes.gid)
    assert nodes.gid.str.fullmatch(r'\d{18}').all()
    assert clusters.n_nodes.sum() == len(nodes)
    assert clusters.n_nodes.min() == 1 and clusters.n_nodes.max() > 60
    assert not nodes.evidence.str.contains('\ufffd').any()


@pytest.mark.parametrize('case', ['missing', 'empty', 'duplicate', 'score', 'mismatch', 'float_gid', 'column'])
def test_invalid_files_explained(tmp_path, real, case):
    for name in ['nodes_roles.csv', 'clusters.csv', 'top_nodes.csv']:
        shutil.copy(ROOT / 'out' / name, tmp_path / name)
    path = tmp_path / 'nodes_roles.csv'
    if case == 'missing':
        path.unlink()
    elif case == 'empty':
        path.write_text('', encoding='utf-8')
    else:
        frame = pd.read_csv(path, dtype=str)
        if case == 'duplicate':
            frame.loc[1, 'gid'] = frame.loc[0, 'gid']
        elif case == 'score':
            frame.loc[0, 'role_score'] = '1.5'
        elif case == 'mismatch':
            top_path = tmp_path / 'top_nodes.csv'
            top = pd.read_csv(top_path, dtype=str)
            top.loc[0, 'priority_score'] = '0'
            top.to_csv(top_path, index=False)
        elif case == 'float_gid':
            frame.loc[0, 'gid'] = '1.000000005e17'
        elif case == 'column':
            frame = frame.drop(columns='evidence')
        frame.to_csv(path, index=False)
    with pytest.raises((ValueError, OSError)):
        load_bundle('data', tmp_path)


def test_warnings_and_isolated(real):
    nodes, _, _, edges = real
    seed = nodes[nodes.is_seed].iloc[0]
    boundary = nodes[(nodes.depth == 4) & (nodes.out_deg == 0)].iloc[0]
    assert 'неполны' in ' '.join(node_warnings(seed))
    assert 'неизвестны' in ' '.join(node_warnings(boundary))
    isolated = nodes[nodes.is_seed & nodes.in_deg.eq(0) & nodes.out_deg.eq(0)].iloc[0]
    selected, available = ego_edges(edges, isolated.gid)
    assert selected.empty and available == 0
    assert list(layout(isolated.gid, selected)) == [isolated.gid]


def test_bounded_directed_graph(real):
    nodes, _, _, edges = real
    gid = nodes.sort_values('out_deg', ascending=False).iloc[0].gid
    selected, available = ego_edges(edges, gid, limit=5)
    neighbors = (set(selected.src) | set(selected.dst)) - {gid}
    assert len(neighbors) <= 5 and available > 5 and len(selected) <= 10
    assert layout(gid, selected) == layout(gid, selected)
    incoming, _ = ego_edges(edges, gid, direction='in')
    outgoing, _ = ego_edges(edges, gid, direction='out')
    assert incoming.dst.eq(gid).all() and outgoing.src.eq(gid).all()
    filtered, _ = ego_edges(edges, gid, minimum=edges.sum_kzt.max() + 1)
    assert filtered.empty
    assert nodes[nodes.gid.eq('unknown')].empty
