"""Synthetic and mock checks verify properties, not real-model accuracy."""
import copy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import time
import pandas as pd
import pytest
from dashboard.ai.config import Settings, AIError, MAX_CONTEXT_BYTES
from dashboard.ai.evidence import CaseData, canonical
from dashboard.ai.tools import Toolset, path_capacity, transit_capacity
from dashboard.ai.schema import validate_final, validate_plan
from dashboard.ai.engine import investigate, ResultCache, markdown_report
from dashboard.ai.provider import OpenAICompatible, Reply, isolated_request
from dashboard.storage import Store


@pytest.fixture

def make_data():
    def make(rows=None, seed=False, depth=1):
        rows = rows or [('1','2','2026-07-01',100.),('2','3','2026-07-02',100.)]
        tx = pd.DataFrame(rows,columns=['src','dst','date','sum_kzt'])
        edges = tx.groupby(['src','dst'],sort=True).agg(sum_kzt=('sum_kzt','sum'),n_tx=('sum_kzt','size')).reset_index()
        edges['depth'] = 1
        records = []
        for gid in sorted(set(tx.src)|set(tx.dst)|{'9'},key=int):
            incoming, outgoing = tx[tx.dst.eq(gid)], tx[tx.src.eq(gid)]
            records.append(dict(gid=gid,role='peripheral',role_score=.4,priority_score=.4,
                in_kzt=float(incoming.sum_kzt.sum()),out_kzt=float(outgoing.sum_kzt.sum()),
                in_tx=len(incoming),out_tx=len(outgoing),in_deg=incoming.src.nunique(),out_deg=outgoing.dst.nunique(),
                depth=depth if gid=='2' else 1,is_seed=seed if gid=='2' else False,
                truncated_by_depth=depth==4 and len(outgoing)==0,priority_part_volume=.4))
        version=sha256((tx.to_csv(index=False)+canonical(records)).encode()).hexdigest()
        return CaseData(pd.DataFrame(records),edges,tx,version)
    return make


@pytest.fixture

def settings():
    return Settings(api_key='test-secret-not-real',model='mock-model',enabled=True,timeout=30,max_tool_calls=5)


def tool(data,name,args):
    return Toolset(data,'2',max_calls=12).execute(name,args)['evidence'][0]


@pytest.mark.parametrize('first,second,possible,strict',[
    ('2026-07-01','2026-07-02',100.,100.),
    ('2026-07-03','2026-07-02',0.,0.),
    ('2026-07-02','2026-07-02',100.,0.),
])
def test_temporal_sequence(make_data,first,second,possible,strict):
    data=make_data([('1','2',first,100.),('2','3',second,100.)])
    values=tool(data,'check_temporal_path',{'path':['1','2','3']})['values']
    assert values['possible_kzt']==possible
    assert values['strictly_increasing_kzt']==strict
    assert values['same_day_needed_for_max']==(possible>strict)
    assert not values['proves_money_trace']


def test_no_double_counting_and_self_transfer(make_data):
    data=make_data([('1','2','2026-07-01',100.),('2','3','2026-07-02',80.),('2','3','2026-07-02',80.),('2','2','2026-07-01',500.)])
    values=tool(data,'find_flow_patterns',{'gid':'2','limits':{'max_transactions':120}})['values']
    assert values['possible_transit_kzt']==100
    assert values['excluded_self_transfers']==1
    assert values['nonadditive_patterns']
    path=tool(data,'check_temporal_path',{'path':['1','2','3']})['values']
    assert path['possible_kzt']==100
    tx=data.tx
    duplicates=tx[tx.src.eq('2') & tx.dst.eq('3')]
    assert duplicates._record_id.nunique()==2
    assert data.version in tool(data,'get_node_profile',{'gid':'2'})['data_hash']


def test_sampling_flags_peers_and_removal_copy(make_data):
    data=make_data(seed=True)
    assert tool(data,'get_node_profile',{'gid':'2'})['values']['inflows_incomplete']
    boundary=make_data([('1','2','2026-07-01',100.)],depth=4)
    assert tool(boundary,'get_node_profile',{'gid':'2'})['values']['truncated_by_depth']
    original=copy.deepcopy(data.graph)
    removal=tool(data,'simulate_node_removal',{'gid':'2'})['values']
    assert removal['lost_reachable_pairs']==1
    assert list(data.graph.nodes)==list(original.nodes)
    assert list(data.graph.edges(data=True))==list(original.edges(data=True))
    peers=tool(data,'compare_peers',{'gid':'2'})['values']
    assert not peers['sufficient'] and peers['group_size']==0
    assert not peers['median_defined']


def test_tool_validation_scope_and_limits(make_data):
    data=make_data()
    tools=Toolset(data,'2',max_calls=1)
    tools.execute('get_node_profile',{'gid':'2'})
    with pytest.raises(AIError,match='лимит'):
        tools.execute('get_node_profile',{'gid':'2'})
    for name,args in [('shell',{'cmd':'whoami'}),('get_neighborhood',{'gid':'2','direction':'out','max_hops':99,'limit':999}),
        ('get_node_profile',{'gid':2}),('get_node_profile',{'gid':'9'}),
        ('check_temporal_path',{'path':['1','2','1']}),('get_node_profile',{'gid':'2','sql':'DROP TABLE users'})]:
        with pytest.raises(AIError):
            Toolset(data,'2').execute(name,args)
    with pytest.raises(AIError,match='дата'):
        Toolset(data,'2').execute('get_transactions',{'gids':['2'],'date_from':'2026-02-31','date_to':'2026-07-31','limit':1})


def plan(gid="2"):
    return {'hypotheses':[{'hypothesis_id':'h1','description':'Возможно транзитное поведение в наблюдаемой выборке'}],
        'checks':[
            {'hypothesis_id':'h1','purpose':'support','tool':{'name':'find_flow_patterns','arguments':{'gid':gid,'limits':{'max_transactions':120}}}},
            {'hypothesis_id':'h1','purpose':'challenge','tool':{'name':'compare_peers','arguments':{'gid':gid}}},
        ]}


def answer_from_context(context):
    fact=next(r for r in context['evidence'] if r['kind']=='flow_patterns')
    ref={'evidence_id':fact['evidence_id'],'gid':fact['gids'][0],**fact['period'],'metric':'possible_transit_kzt','value':fact['values']['possible_transit_kzt']}
    return {'summary':'Наблюдения допускают транзитную гипотезу, но данных для установления назначения недостаточно',
        'hypotheses':[{'hypothesis_id':'h1','description':'Возможное транзитное поведение','status':'недостаточно данных',
            'supporting':[ref],'contradicting':[],'alternatives':['Переводы могут быть независимыми'],
            'missing_data':['Неизвестно назначение переводов'],
            'checks':[c['check_id'] for c in context['checks'] if c.get('hypothesis_id')=='h1'],
            'next_step':'Запросить сведения о назначении переводов'}]}


class MockProvider:
    def __init__(self, bad=None):
        self.calls=[]
        self.bad=bad
    def complete(self,messages,deadline):
        self.calls.append(copy.deepcopy(messages))
        payload=json.loads(messages[1]['content'])
        if self.bad=='json':
            return Reply('invalid JSON')
        if payload['phase']=='plan':
            return Reply(canonical(plan(payload['data']['focus_gid'])),{'total_tokens':12})
        answer=answer_from_context(payload['data'])
        ref=answer['hypotheses'][0]['supporting'][0]
        if self.bad=='evidence':ref['evidence_id']='ev_'+'0'*20
        if self.bad=='amount':ref['value']=999999
        if self.bad=='gid':ref['gid']='999'
        if self.bad=='period':ref['date_to']='2026-08-01'
        if self.bad=='prose':answer['summary']='Переведено 999999 KZT'
        return Reply(canonical(answer),{'total_tokens':20})


def test_orchestration_and_cache(make_data,settings,tmp_path):
    store=Store(tmp_path/'cache.db')
    user=store.user('test','1','test@example.test','Test')
    cache=ResultCache(store,user)
    provider=MockProvider()
    data=make_data()
    result=investigate(data,'2',settings,cache,provider)
    assert result['ok'] and not result['cache_hit']
    assert len(result['checks'])==3 and len(provider.calls)==2
    assert result['usage']=={'total_tokens':32}
    again=investigate(data,'2',settings,cache,provider)
    assert again['cache_hit'] and len(provider.calls)==2
    changed=make_data([('1','2','2026-07-01',120.),('2','3','2026-07-02',100.)])
    assert changed.version!=data.version
    assert not investigate(changed,'2',settings,cache,provider)['cache_hit']
    assert settings.cache_key(data.version,'2')!=replace(settings,model='different').cache_key(data.version,'2')
    assert settings.cache_key(data.version,'2')==replace(settings,api_key='another-secret').cache_key(data.version,'2')
    assert settings.api_key not in canonical(provider.calls)
    assert settings.api_key not in markdown_report(result)
    assert 'source_rows' not in canonical(provider.calls)
    assert len(canonical(provider.calls[-1]).encode())<MAX_CONTEXT_BYTES


@pytest.mark.parametrize('bad',['evidence','amount','gid','period','prose','json'])
def test_invalid_output_one_repair_no_success_cache(make_data,settings,tmp_path,bad):
    store=Store(tmp_path/'cache.db'); user=store.user('test','1','','Test'); cache=ResultCache(store,user)
    provider=MockProvider(bad)
    result=investigate(make_data(),'2',settings,cache,provider)
    assert not result['ok'] and result['evidence']['records']
    assert len(provider.calls)==(2 if bad=='json' else 3)
    assert cache.get(settings.cache_key(make_data().version,'2')) is None


def test_plan_requires_real_countercheck():
    validate_plan(plan(),4)
    for invalid in [dict(plan(),checks=plan()['checks'][:1]),dict(plan(),checks=plan()['checks']*3)]:
        with pytest.raises(AIError):validate_plan(invalid,4)


def test_provider_rate_limits_timeout_and_malformed_json(settings):
    replies=iter([{'status':429},{'status':503},{'status':200,'data':{'choices':[{'message':{'content':'{}'}}]}}])
    provider=OpenAICompatible(settings,lambda p,t:next(replies))
    assert provider.complete([],time.monotonic()+5).content=='{}'
    assert provider.retries_left==0
    for result in [{'error':'timeout'},{'error':'invalid_json'},{'status':200,'data':{}},{'status':401}]:
        with pytest.raises(AIError):
            OpenAICompatible(settings,lambda p,t:result).complete([],time.monotonic()+1)
    calls=[]
    def rate(p,t):calls.append(1);return {'status':429}
    with pytest.raises(AIError,match='частоту'):
        OpenAICompatible(settings,rate).complete([],time.monotonic()+5)
    assert len(calls)==3


def test_deadline_and_secret_safety(make_data,settings):
    class Slow:
        def complete(self,messages,deadline):
            time.sleep(.03)
            return Reply('{}')
    # Already consumed deadline: no provider call is allowed.
    result=investigate(make_data(),'2',settings,provider=Slow(),started=time.monotonic()-settings.timeout-1)
    assert not result['ok'] and 'дедлайн' in result['error']
    secret=settings.api_key
    provider=OpenAICompatible(settings,lambda p,t:{'status':200,'data':{'choices':[{'message':{'content':secret}}]}})
    assert secret not in provider.complete([],time.monotonic()+1).content
    with pytest.raises(AIError) as caught:
        def explode(p,t):raise RuntimeError(secret)
        OpenAICompatible(settings,explode).complete([],time.monotonic()+1)
    assert secret not in str(caught.value)


def test_isolated_transport_kills_expired_process(monkeypatch):
    class Process:
        returncode=0
        killed=False
        def communicate(self,*a,**kw):
            if not self.killed:raise subprocess.TimeoutExpired('worker',.01)
            return b'',b''
        def kill(self):self.killed=True
    process=Process()
    monkeypatch.setattr('dashboard.ai.provider.subprocess.Popen',lambda *a,**kw:process)
    with pytest.raises(AIError,match='дедлайн'):
        isolated_request({'safe':'test'},.01)
    assert process.killed


def test_disabled_ai_leaves_facts(make_data,settings):
    provider=MockProvider()
    result=investigate(make_data(),'2',replace(settings,api_key=''),provider=provider)
    assert not result['ok'] and not provider.calls
    assert result['evidence']['records']


def test_ai_ui_button_cache_rerun_and_local_removal(monkeypatch,tmp_path):
    from streamlit.testing.v1 import AppTest
    from dashboard.accounts import Accounts
    from dashboard.data import ROOT
    if not (ROOT/'out/nodes_roles.csv').exists():
        pytest.skip('Local real CSV required')
    for name,value in {'AI_ENABLED':'true','AI_API_KEY':'test-secret-not-real','AI_MODEL':'mock-model','AI_TIMEOUT_SECONDS':'30','AI_MAX_TOOL_CALLS':'5'}.items():
        monkeypatch.setenv(name,value)
    db_path=tmp_path/'ui.db'
    monkeypatch.setenv('MONEYGRAPH_DB',str(db_path))
    accounts=Accounts(Store(db_path))
    accounts.register('Analyst','ai@example.test','correct horse battery staple')
    token=accounts.login('ai@example.test','correct horse battery staple')
    provider=MockProvider()
    monkeypatch.setattr('dashboard.ai.engine.OpenAICompatible',lambda settings:provider)
    app=AppTest.from_file(str(ROOT/'dashboard/app.py'),default_timeout=30)
    app.session_state['_auth_token']=token
    app.run()
    assert not app.exception and not provider.calls
    app.sidebar.radio[0].set_value('Узел и переводы').run()
    assert not app.exception and not provider.calls
    assert app.button(key='ai_run').disabled
    next(c for c in app.checkbox if 'Разрешаю' in c.label).check().run()
    app.button(key='ai_run').click().run()
    assert not app.exception
    result=app.session_state['_ai_result'][2]
    assert result['ok'],result.get('error')
    assert len(provider.calls)==2 and not result['cache_hit']
    app.run()
    assert len(provider.calls)==2
    app.button(key='ai_run').click().run()
    assert not app.exception
    assert app.session_state['_ai_result'][2]['cache_hit']
    assert len(provider.calls)==2
    app.button(key='ai_remove').click().run()
    assert not app.exception
    assert app.session_state['_ai_removal'][1]['records']
    assert len(provider.calls)==2


def test_worker_error_bodies_not_exposed(monkeypatch):
    from dashboard.ai.http_worker import request
    class Response:
        status_code=401
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,*args):raise AssertionError('Must not read error body')
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):
            assert kwargs['allow_redirects'] is False
            return Response()
    monkeypatch.setattr('dashboard.ai.http_worker.requests.Session',Session)
    assert request({'url':'https://provider.example/v1/chat/completions','payload':{},'key':'secret','timeout':1})=={'status':401}


def test_one_repair_can_succeed_but_is_shared_between_phases(make_data,settings):
    class Repair(MockProvider):
        def complete(self,messages,deadline):
            if not self.calls:
                self.calls.append(copy.deepcopy(messages))
                return Reply('{broken')
            return super().complete(messages,deadline)
    repaired=Repair()
    result=investigate(make_data(),'2',settings,provider=repaired)
    assert result['ok'] and len(repaired.calls)==3
    exhausted=Repair('amount')
    result=investigate(make_data(),'2',settings,provider=exhausted)
    assert not result['ok'] and len(exhausted.calls)==3


def test_scope_is_bounded_and_cache_is_per_user(make_data,settings,tmp_path):
    rows=[('2',str(i),'2026-07-01',100.) for i in range(10,200)]
    data=make_data(rows)
    tools=Toolset(data,'2')
    assert len(tools.scope)==80
    result=tools.execute('get_neighborhood',{'gid':'2','direction':'out','max_hops':1,'limit':30})
    assert result['evidence'][0]['values']['returned_nodes']==30
    assert result['evidence'][0]['values']['truncated']
    store=Store(tmp_path/'cache.db')
    first=store.user('test','1','','First');second=store.user('test','2','','Second')
    cache=ResultCache(store,first)
    result=investigate(make_data(),'2',settings,cache,MockProvider())
    assert result['ok']
    key=settings.cache_key(make_data().version,'2')
    assert ResultCache(store,second).get(key) is None


def test_missing_model_and_invalid_endpoint_errors_are_safe(settings):
    for changed in [replace(settings,model=''),replace(settings,base_url='https://['),
                    replace(settings,base_url='https://user:secret@example.test/v1'),replace(settings,base_url='http://example.test/v1')]:
        with pytest.raises(AIError) as error:
            changed.validate()
        assert settings.api_key not in str(error.value)


def test_langgraph_execution_and_language_cache_separation(make_data, settings, tmp_path, monkeypatch):
    from langgraph.graph.state import CompiledStateGraph
    invoked = []
    original = CompiledStateGraph.invoke
    def invoke(self, state, config=None, **kwargs):
        invoked.append(config['recursion_limit'])
        return original(self, state, config, **kwargs)
    monkeypatch.setattr(CompiledStateGraph, 'invoke', invoke)
    store = Store(tmp_path / 'languages.db')
    cache = ResultCache(store, store.user('test', 'language', '', 'Test'))
    data = make_data()
    provider = MockProvider()
    for locale, prompt_text in [('ru', 'по-русски'), ('en', 'на английском языке'), ('kk', 'на казахском языке')]:
        config = replace(settings, language=locale)
        result = investigate(data, '2', config, cache, provider)
        assert result['ok'] and result['orchestration'] == 'langgraph'
        assert result['language'] == locale and not result['cache_hit']
        assert prompt_text in provider.calls[-1][0]['content']
        assert investigate(data, '2', config, cache, provider)['cache_hit']
    assert invoked == [settings.max_tool_calls + 4] * 3
    assert len(provider.calls) == 6
