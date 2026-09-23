"""Locale switching must preserve identity and never start a paid investigation."""
import ast
import csv
import re
from pathlib import Path
from string import Formatter

import pytest
from streamlit.testing.v1 import AppTest
from dashboard.accounts import Accounts
from dashboard.data import ROOT
from dashboard.storage import Store


def test_catalog_integrity_and_display_coverage():
    rows = list(csv.reader((ROOT / 'dashboard/locales.tsv').open(), delimiter='\t'))
    assert all(len(row) == 3 and all(part.strip() for part in row) for row in rows)
    entries = {row[0]: row[1:] for row in rows}
    assert len(entries) == len(rows)
    fields = lambda text: {name for _, name, _, _ in Formatter().parse(text) if name is not None}
    for source, translations in entries.items():
        assert all(fields(source) == fields(value) for value in translations), source
    for name in ('app.py', 'auth.py', 'ui.py', 'welcome.py', 'ai/ui.py'):
        for node in ast.walk(ast.parse((ROOT / 'dashboard' / name).read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 't' and node.args:
                value = node.args[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and re.search('[А-Яа-я]', value.value):
                    assert value.value in entries, value.value


@pytest.mark.parametrize('locale,login_label,password_label', [('en', 'Sign in', 'Password'), ('kk', 'Кіру', 'Құпиясөз')])
def test_locale_survives_login_logout_and_navigation(monkeypatch, tmp_path, locale, login_label, password_label):
    path = tmp_path / 'locale.db'
    monkeypatch.setenv('MONEYGRAPH_DB', str(path))
    monkeypatch.setenv('AI_ENABLED', 'false')
    accounts = Accounts(Store(path))
    password = 'test language long password'
    accounts.register('Test Analyst', 'locale@example.test', password)
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30).run()
    app.selectbox(key='language').set_value(locale).run()
    assert app.button(key='welcome_login').label == login_label
    app.button(key='welcome_login').click().run()
    assert app.text_input(key='login_password').label == password_label
    app.text_input(key='login_email').set_value('locale@example.test')
    app.text_input(key='login_password').set_value(password)
    next(button for button in app.button if button.label == login_label).click().run()
    assert not app.exception
    assert app.selectbox(key='language').value == locale
    assert not any(button.key == 'logout' for button in app.sidebar.button)
    for section in ('Узел и переводы', 'Кластеры', 'Мои проверки', 'Проверяемость', 'AI-расследователь'):
        app.radio(key='section').set_value(section).run()
        assert not app.exception
    assert app.button(key='ai_run').disabled
    app.button(key='logout').click().run()
    assert not app.exception and app.selectbox(key='language').value == locale
    assert app.text_input(key='login_password').label == password_label
    assert not app.dataframe


def test_manual_queue_requires_click_and_retries_failed_candidate(monkeypatch, tmp_path):
    from dashboard.ai.config import Settings
    from dashboard.ai import ui
    path = tmp_path / 'queue.db'
    monkeypatch.setenv('MONEYGRAPH_DB', str(path))
    accounts = Accounts(Store(path))
    accounts.register('Queue Test', 'queue@example.test', 'queue test long password')
    token = accounts.login('queue@example.test', 'queue test long password')
    monkeypatch.setattr(Settings, 'from_env', classmethod(lambda cls: Settings(enabled=True, model='mock', api_key='fake-secret')))
    calls = []
    def mock_investigate(data, gid, settings, *args, **kwargs):
        calls.append((gid, settings.language))
        return dict(ok=len(calls) > 1, gid=gid, language=settings.language, data_hash=data.version,
                    model='mock', seconds=0, cache_hit=False, evidence={'records': {}, 'source_rows': {}},
                    checks=[], limitations=[], answer={'summary': 'Mock', 'hypotheses': []})
    monkeypatch.setattr(ui, 'investigate', mock_investigate)
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30)
    app.session_state['_auth_token'] = token
    app.run().radio(key='section').set_value('AI-расследователь').run()
    assert not calls and app.button(key='ai_run').disabled
    app.checkbox(key='ai_queue_consent').check().run()
    assert not calls
    app.button(key='ai_run').click().run()
    assert len(calls) == 1 and not app.exception
    app.run()
    assert len(calls) == 1
    app.button(key='ai_run').click().run()
    assert len(calls) == 2 and calls[0][0] == calls[1][0] and not app.exception
    app.button(key='ai_run').click().run()
    assert len(calls) == 3 and calls[2][0] != calls[1][0] and not app.exception
    app.selectbox(key='language').set_value('en').run()
    app.run()
    assert app.radio(key='section').value == 'AI-расследователь'
    assert len(calls) == 3
    app.radio(key='ai_mode').set_value('Выбрать участника').run()
    assert not app.exception and len(app.selectbox(key='ai_selected_gid').options) == 2248
    app.selectbox(key='ai_selected_gid').select_index(5).run()
    assert len(calls) == 3
