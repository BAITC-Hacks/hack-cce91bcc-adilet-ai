"""Integration smoke against real outputs; no synthetic clients in the app."""
import pytest
pytest.importorskip('streamlit', reason='Установите dashboard/requirements.txt для Streamlit AppTest')
pytest.importorskip('plotly', reason='Установите dashboard/requirements.txt для Plotly')
from streamlit.testing.v1 import AppTest
from dashboard.data import ROOT, load_bundle


PASSWORD = 'correct horse battery staple'


def register_and_login(app, email='analyst@example.test'):
    if any(b.key == 'welcome_register' for b in app.button):
        app.button(key='welcome_register').click().run()
    app.radio(key='auth_mode').set_value('Регистрация').run()
    app.text_input(key='register_name').set_value('Аналитик')
    app.text_input(key='register_email').set_value(email)
    app.text_input(key='register_password').set_value(PASSWORD)
    app.text_input(key='register_repeat').set_value(PASSWORD)
    next(b for b in app.button if b.label == 'Создать аккаунт').click().run()
    assert not app.exception
    recovery = app.code[0].value
    app.button(key='recovery_ack').click().run()
    login(app, email)
    return recovery


def login(app, email='analyst@example.test', password=PASSWORD):
    if any(b.key == 'welcome_login' for b in app.button):
        app.button(key='welcome_login').click().run()
    app.text_input(key='login_email').set_value(email)
    app.text_input(key='login_password').set_value(password)
    next(b for b in app.button if b.label == 'Войти').click().run()
    assert not app.exception


def test_real_app_navigation(monkeypatch, tmp_path):
    monkeypatch.setenv('MONEYGRAPH_DB', str(tmp_path / 'workspace.sqlite3'))
    if not (ROOT / 'out/nodes_roles.csv').exists():
        pytest.skip('Сначала сформируйте реальные CSV')
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30).run()
    assert not app.exception and not app.dataframe
    register_and_login(app)
    assert [metric.value for metric in app.metric][:3] == ['2 248', '3 119', '4 840']
    app.sidebar.radio[0].set_value('Узел и переводы').run()
    assert not app.exception
    assert len(app.metric) == 3
    assert any('Кто выше' in element.value for element in app.subheader)
    assert any('Почему такой приоритет' in element.label for element in app.expander)
    app.text_area[0].set_value('Проверить наблюдаемые источники')
    next(b for b in app.button if b.label == 'Сохранить в мои проверки').click().run()
    assert not app.exception and app.success
    app.sidebar.radio[0].set_value('Мои проверки').run()
    assert not app.exception
    assert any('Проверить наблюдаемые источники' in text.value for text in app.text)
    app.sidebar.radio[0].set_value('Узел и переводы').run()
    app.text_input[0].set_value('unknown').run()
    assert not app.exception and any('не найден' in warning.value for warning in app.warning)
    nodes, clusters, _, _ = load_bundle('data', 'out')
    for row in [nodes[nodes.is_seed & nodes.in_deg.eq(0) & nodes.out_deg.eq(0)].iloc[0], nodes[nodes.depth.eq(4) & nodes.out_deg.eq(0)].iloc[0]]:
        app.text_input[0].set_value(row.gid).run()
        assert not app.exception and app.warning
    app.sidebar.radio[0].set_value('Кластеры').run()
    for size in [clusters.n_nodes.min(), clusters.n_nodes.max()]:
        gid = clusters.loc[clusters.n_nodes.eq(size), 'cluster_id'].iloc[0]
        app.selectbox[0].set_value(gid).run()
        assert not app.exception and app.metric[0].value == str(int(size))
    app.sidebar.radio[0].set_value('Проверяемость').run()
    assert not app.exception
    if (ROOT / 'out/run_report.json').exists():
        assert app.success
    app.sidebar.text_input[1].set_value('/tmp/moneygraph-missing-ui-input').run()
    assert not app.exception and app.error and app.code


def test_account_workspace_survives_new_session(monkeypatch, tmp_path):
    import streamlit as st
    monkeypatch.setattr(st, 'secrets', {})
    db_path = tmp_path / 'workspace.sqlite3'
    monkeypatch.setenv('MONEYGRAPH_DB', str(db_path))
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30).run()
    assert not app.exception and not app.dataframe
    register_and_login(app)
    assert not any('Google' in button.label for button in app.button)
    app.sidebar.radio[0].set_value('Узел и переводы').run()
    app.text_area[0].set_value('Заметка между сессиями')
    next(b for b in app.button if b.label == 'Сохранить в мои проверки').click().run()
    assert not app.exception and app.success
    fresh = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30).run()
    assert not fresh.dataframe
    login(fresh)
    fresh.sidebar.radio[0].set_value('Мои проверки').run()
    assert not fresh.exception
    assert any('Заметка между сессиями' in text.value for text in fresh.text)
    from dashboard.storage import Store
    with Store(db_path).connect() as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1


def test_registration_logout_recovery_and_account_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv('MONEYGRAPH_DB', str(tmp_path / 'accounts.db'))
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30).run()
    recovery = register_and_login(app, 'first@example.test')
    app.sidebar.radio[0].set_value('Узел и переводы').run()
    app.text_area[0].set_value('Личная заметка первого пользователя')
    next(b for b in app.button if b.label == 'Сохранить в мои проверки').click().run()
    token = app.session_state['_auth_token']
    app.button(key='logout').click().run()
    assert not app.exception and not app.dataframe
    register_and_login(app, 'second@example.test')
    app.sidebar.radio[0].set_value('Мои проверки').run()
    assert not any('Личная заметка первого пользователя' in t.value for t in app.text)
    app.button(key='logout').click().run()
    login(app, 'first@example.test', 'wrong-password-long-enough')
    assert app.error and not app.dataframe
    app.radio(key='auth_mode').set_value('Восстановление').run()
    app.text_input(key='reset_email').set_value('first@example.test')
    app.text_input(key='reset_code').set_value(recovery)
    app.text_input(key='reset_password').set_value('new correct horse battery staple')
    app.text_input(key='reset_repeat').set_value('new correct horse battery staple')
    next(b for b in app.button if b.label == 'Восстановить доступ').click().run()
    assert not app.exception and app.code[0].value != recovery
    app.button(key='recovery_ack').click().run()
    login(app, 'first@example.test', 'new correct horse battery staple')
    app.sidebar.radio[0].set_value('Мои проверки').run()
    assert any('Личная заметка первого пользователя' in t.value for t in app.text)
    app.text_input(key='old_password').set_value('new correct horse battery staple')
    app.text_input(key='new_password').set_value(PASSWORD)
    app.text_input(key='new_repeat').set_value(PASSWORD)
    next(b for b in app.button if b.label == 'Сменить пароль').click().run()
    assert not app.exception and not app.dataframe
    login(app, 'first@example.test')
    assert app.dataframe
    from dashboard.accounts import Accounts
    from dashboard.storage import Store
    assert Accounts(Store(tmp_path / 'accounts.db')).identity(token) is None


def test_forged_session_has_no_access(monkeypatch, tmp_path):
    monkeypatch.setenv('MONEYGRAPH_DB', str(tmp_path / 'accounts.db'))
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30)
    app.session_state['_auth_token'] = 'forged-session-token-123456789'
    app.session_state['signed_in_user'] = 1
    app.run()
    assert not app.exception and not app.dataframe and not app.metric
    assert app.text_input(key='login_email') is not None


def test_welcome_navigation_and_failed_login_email(monkeypatch, tmp_path):
    monkeypatch.setenv('MONEYGRAPH_DB', str(tmp_path / 'welcome.db'))
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30).run()
    assert not app.exception and not app.text_input and not app.dataframe
    app.button(key='welcome_login').click().run()
    assert app.checkbox[0].label == 'Запомнить меня на 30 дней'
    login(app, 'absent@example.test', 'wrong password')
    assert app.error and app.text_input(key='login_email').value == 'absent@example.test'
    app.button(key='auth_back').click().run()
    assert not app.text_input
    app.button(key='welcome_register').click().run()
    assert app.text_input(key='register_repeat') and not app.exception


def test_browser_token_restores_only_valid_account(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from dashboard.accounts import Accounts
    from dashboard.storage import Store
    db = tmp_path / 'remember.db'
    monkeypatch.setenv('MONEYGRAPH_DB', str(db))
    accounts = Accounts(Store(db))
    accounts.register('Remembered', 'remember@example.test', PASSWORD)
    token = accounts.login('remember@example.test', PASSWORD, remember=True)
    monkeypatch.setattr('dashboard.auth.browser_session', lambda: SimpleNamespace(token=token))
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30).run()
    assert not app.exception and app.button(key='logout')
    accounts.logout(token)
    fresh = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30).run()
    assert not fresh.exception and not fresh.dataframe
    assert fresh.button(key='welcome_login')
