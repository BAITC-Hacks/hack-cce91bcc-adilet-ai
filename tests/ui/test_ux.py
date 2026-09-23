"""Failure paths that matter to an analyst, beyond happy-path navigation."""
import pytest
pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest
from dashboard.accounts import Accounts
from dashboard.storage import Store, StorageError
from dashboard.data import ROOT


def signed_app(monkeypatch, tmp_path):
    path = tmp_path / 'qa.sqlite3'
    monkeypatch.setenv('MONEYGRAPH_DB', str(path))
    store = Store(path)
    accounts = Accounts(store)
    accounts.register('Аналитик', 'qa@example.test', 'a long test password phrase')
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30)
    app.session_state['_auth_token'] = accounts.login('qa@example.test', 'a long test password phrase')
    return app.run(), store


def test_database_outage_is_actionable_and_does_not_show_secrets(monkeypatch):
    def fail():
        raise StorageError('sensitive-host:user:password')
    monkeypatch.setattr('dashboard.storage.open_store', fail)
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30).run()
    assert not app.exception and not app.dataframe
    assert any(b.label == 'Повторить подключение' for b in app.button)
    assert not any('sensitive' in e.value for e in app.error)


def test_saved_notes_available_when_source_files_disappear(monkeypatch, tmp_path):
    app, store = signed_app(monkeypatch, tmp_path)
    account = Accounts(store).identity(app.session_state['_auth_token'])
    dataset = store.snapshot({'out/nodes_roles.csv': b'gid\n123\n'})
    store.save_case(account['id'], dataset, '123', 'В работе', 'Архивная заметка', 'Report')
    app.sidebar.radio[0].set_value('Мои проверки').run()
    app.sidebar.text_input[0].set_value(str(tmp_path / 'missing'))
    app.sidebar.text_input[1].set_value(str(tmp_path / 'missing')).run()
    assert not app.exception
    assert any(t.value == 'Архивная заметка' for t in app.text)
    assert any('Архивная версия' in c.value for c in app.caption)
    next(s for s in app.selectbox if s.label == 'Показать проверки').set_value('Проверено').run()
    assert any('Нет проверок с такими условиями' in e.value for e in app.info)


def test_save_failure_preserves_note_and_unknown_gid_can_recover(monkeypatch, tmp_path):
    if not (ROOT / 'out/nodes_roles.csv').exists():
        pytest.skip('Local dataset required')
    app, store = signed_app(monkeypatch, tmp_path)
    app.sidebar.radio[0].set_value('Узел и переводы').run()
    assert not app.exception
    app.text_area[0].set_value('Несохранённая важная заметка')
    def fail(*args, **kwargs):
        raise StorageError('sensitive SQL')
    monkeypatch.setattr(Store, 'save_case', fail)
    next(b for b in app.button if b.label == 'Сохранить в мои проверки').click().run()
    assert not app.exception and app.error
    assert app.text_area[0].value == 'Несохранённая важная заметка'
    app.text_input(key='node_gid').set_value("' OR 1=1 --").run()
    assert not app.exception and app.warning
    next(b for b in app.button if b.label == 'Открыть первого в списке приоритета').click().run()
    assert not app.exception and app.metric
