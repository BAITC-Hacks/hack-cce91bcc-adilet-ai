import pandas as pd
import pytest
from dashboard.investigation import upstream, case_note


def test_directed_bounded_upstream_and_cycles():
    # Synthetic fixture: 5 -> 4 -> 3 -> 2 -> 1, plus outgoing 1 -> 6.
    nodes=pd.DataFrame({'gid':list('123456'),'role':['transit']*6,'priority_score':[.1,.2,.3,.4,.5,.6]})
    edges=pd.DataFrame({'src':list('543216'),'dst':list('432166')})
    result=upstream(nodes,edges,'1',cutoff=2)
    assert set(result.gid)=={'2','3'}
    assert result.iloc[0].path=='3 → 2 → 1'
    assert set(upstream(nodes,edges,'1',cutoff=4).gid)==set('2345')
    assert len(upstream(nodes,edges,'1',limit=1))==1
    with pytest.raises(ValueError):
        upstream(nodes,edges,'1',cutoff=5)


def test_note_is_based_on_observations():
    row=pd.Series({'gid':'123','role':'peripheral','role_score':.35,'priority_score':.1,'evidence':'in=0;out=0'})
    text=case_note(row,pd.DataFrame())
    assert '123' in text and 'in=0;out=0' in text
    assert 'не движение конкретной суммы' in text
