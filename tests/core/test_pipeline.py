"""Synthetic boundary tests and optional local dataset contract."""
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from src.load import load, validate
from src.graph import build_graph, centrality
from src.features import basic_features
from src.temporal import possible_fast_volume, temporal_features
from src.communities import communities
from src.roles import assign_roles, scale
from src.export import outputs, validate_outputs

@pytest.mark.parametrize('incoming,outgoing,expected', [
    ({1:100}, {1:100}, 100), ({1:100}, {3:100}, 100),
    ({1:100}, {4:100}, 0), ({2:100}, {1:100}, 0),
    ({1:100}, {1:60,2:60}, 100), ({1:60,2:60}, {2:100,3:100},120),
    ({}, {1:100}, 0)])
def test_temporal(incoming,outgoing,expected):
    assert possible_fast_volume(incoming,outgoing) == expected

def fixture():
    nodes = pd.DataFrame({'gid':[1,2,3,4,5], 'depth':[0,1,4,0,2], 'is_seed':[True,False,False,True,False]})
    tx = pd.DataFrame({'src':[1,2,2], 'dst':[2,3,5], 'sum_kzt':[100.,60.,40.], 'date':pd.to_datetime(['2026-07-01']*3)})
    edges = tx.groupby(['src','dst'],as_index=False).agg(sum_kzt=('sum_kzt','sum'),n_tx=('sum_kzt','size'))
    edges['depth'] = [1,4,2]
    return edges,nodes,tx

def calculate(edges,nodes,tx):
    graph=build_graph(edges,nodes)
    df=basic_features(graph,nodes)
    df=temporal_features(df,tx)
    df=centrality(graph,df)
    return assign_roles(communities(graph,df))

def test_validation():
    edges,nodes,tx=fixture()
    validate(edges,nodes,tx)
    for column,delta in [('sum_kzt',.02),('n_tx',1)]:
        bad=edges.copy()
        bad.loc[0,column]+=delta
        with pytest.raises(ValueError,match='mismatch'):
            validate(bad,nodes,tx)
    with pytest.raises(ValueError,match='integers'):
        validate(edges,nodes.astype({'gid':float}),tx)
    with pytest.raises(ValueError,match='Duplicate'):
        validate(edges,pd.concat([nodes,nodes.iloc[:1]]),tx)

def test_boundaries_and_contract():
    edges,nodes,tx=fixture()
    df=calculate(edges,nodes,tx)
    rows=df.set_index('gid')
    assert rows.loc[4,'pagerank']>0
    assert rows.loc[4,'role']=='peripheral'
    assert rows.loc[4,'role_score']<=.35
    assert rows.loc[4,'missing_inflow'] and rows.loc[4,'pass_through']==0
    assert rows.loc[3,'role']!='terminal' and rows.loc[3,'truncated_by_depth']
    assert rows.loc[2,'fast2']==1 and rows.loc[2,'seed_sources']==1
    assert rows.loc[1,'seed_sources']==0
    assert (df.loc[df.is_seed,['score_transit','score_consolidator','score_terminal']]==0).all().all()
    validate_outputs(*outputs(df,edges))
    np.testing.assert_array_equal(scale([0,0]),[0,0])
    other=calculate(edges.sample(frac=1,random_state=1),nodes,tx.sample(frac=1,random_state=2))
    pd.testing.assert_frame_equal(df,other)
    df['priority_score']=.5
    exported=outputs(df.sample(frac=1,random_state=3),edges)
    assert exported[2].gid.tolist()==sorted(nodes.gid)
    validate_outputs(*exported)

@pytest.mark.skipif(not Path('data/nodes.parquet').exists(),reason='Private dataset absent')
def test_real_contract():
    edges,nodes,tx=load('data')
    assert (len(nodes),len(edges),len(tx),nodes.is_seed.sum())==(2248,3119,4840,81)
    df=calculate(edges,nodes,tx)
    assert ((df.in_deg+df.out_deg)==0).sum()==19
    assert df.truncated_by_depth.sum()==444
    validate_outputs(*outputs(df,edges))
