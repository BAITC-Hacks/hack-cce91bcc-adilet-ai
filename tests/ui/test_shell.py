"""UI regressions that do not require the private parquet dataset."""
import pytest
pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest
from dashboard.accounts import Accounts
from dashboard.storage import Store
from dashboard.data import ROOT


def test_authenticated_empty_workspace(monkeypatch, tmp_path):
    db = tmp_path / 'workspace.db'
    monkeypatch.setenv('MONEYGRAPH_DB', str(db))
    accounts = Accounts(Store(db))
    accounts.register('Analyst <script>', 'ui@example.test', 'a sufficiently long test password')
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30)
    app.session_state['_auth_token'] = accounts.login('ui@example.test', 'a sufficiently long test password')
    # Pin missing paths so the test also works on machines with a real dataset.
    app.run()
    app.sidebar.text_input[0].set_value(str(tmp_path / 'missing-data'))
    app.sidebar.text_input[1].set_value(str(tmp_path / 'missing-output')).run()
    assert not app.exception
    assert [metric.value for metric in app.metric] == ['—'] * 4
    assert app.sidebar.radio[0].value == 'Обзор'
    assert any('Подключите данные' in item.value for item in app.info)
    app.button(key='logout').click().run()
    assert not app.exception and not app.metric
    assert app.text_input(key='login_email')
