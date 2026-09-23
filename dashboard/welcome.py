"""Public welcome screen and browser persistence for revocable session tokens."""
from pathlib import Path
import streamlit as st
from dashboard.i18n import t, html_t, language
import streamlit.components.v2 as components
from dashboard.ui import brand_html
ASSETS = Path(__file__).parent / 'frontend'

def _session_component():
    return components.component('moneygraph_browser_session', html='<span></span>', js="\nexport default function({data, parentElement, setStateValue}) {\n    const marker = parentElement.querySelector('span');\n    const publish = (token, error) => {\n        const signature = JSON.stringify([token, error]);\n        if (marker.dataset.result === signature) return;\n        marker.dataset.result = signature;\n        setStateValue('token', token);\n        setStateValue('error', error);\n    };\n    const key = 'moneygraph.session.v1';\n    try {\n        if (data.action === 'clear') localStorage.removeItem(key);\n        if (data.action === 'save') localStorage.setItem(key, JSON.stringify({\n            token: data.token, expires: Date.now() + 30 * 86400000\n        }));\n        let token = null;\n        if (data.action === 'read') {\n            const saved = JSON.parse(localStorage.getItem(key) || 'null');\n            if (saved && saved.expires > Date.now() && typeof saved.token === 'string') token = saved.token;\n            else localStorage.removeItem(key);\n        }\n        publish(token, false);\n    } catch (_) {\n        publish(null, true);\n    }\n}\n")

def browser_session():
    result = _session_component()(key='browser_session', data=st.session_state.get('_browser_action', {'action': 'read'}), default={'token': None, 'error': False}, on_token_change=lambda: None, on_error_change=lambda: None, height=0)
    if result.error:
        st.caption(t('Браузер запретил сохранение входа. В этой вкладке вход работает как обычно.'))
    return result

def _open(mode):
    st.session_state['_auth_page'] = True
    st.session_state['auth_mode'] = mode

def landing():
    with st.container(key='welcome'):
        st.html(html_t('<div class="mg-welcome-head">' + brand_html() + '\n            <small>ГРАФ ДЕНЕГ · HACKALEM AI</small></div>\n            <div class="mg-welcome-copy"><span class="mg-login-tag">ОТ ПЕРЕВОДА К ПОЛНОЙ КАРТИНЕ</span>\n            <h1>У каждого перевода<br>есть продолжение.</h1>\n            <p>Увидьте цепочки движения денег, исследуйте связи<br>\n            и соберите наблюдения в личном пространстве аналитика.</p></div>'))
        with st.container(key='welcome_actions', horizontal=True, horizontal_alignment='center'):
            st.button(t('Войти'), key='welcome_login', type='primary', icon=':material/arrow_forward:', on_click=_open, args=('Вход',))
            st.button(t('Создать аккаунт'), key='welcome_register', on_click=_open, args=('Регистрация',))
        hero = ASSETS / 'chain-hero.png'
        if hero.exists():
            st.image(str(hero), width='stretch')
        st.html(html_t('<div class="mg-welcome-features">\n            <div><small>01 / ИССЛЕДУЙТЕ</small><h3>Связи в одной картине</h3><p>Направленный граф помогает проследить наблюдаемые переводы.</p></div>\n            <div><small>02 / ПРОВЕРЯЙТЕ</small><h3>Числа за каждым выводом</h3><p>Роли и приоритеты сопровождаются наблюдениями и ограничениями данных.</p></div>\n            <div><small>03 / СОХРАНЯЙТЕ</small><h3>Своё пространство</h3><p>Личные заметки и проверки доступны после следующего входа.</p></div></div>\n            <section class="mg-stack"><span class="mg-login-tag">ПОД КАПОТОМ</span>\n            <h2>Прозрачная аналитика. Понятные инструменты.</h2>\n            <div class="mg-stack-grid">\n            <div><b>Python · pandas · NumPy</b><p>Подготовка данных и расчёт признаков</p></div>\n            <div><b>NetworkX</b><p>Граф, центральности и кластеры связей</p></div>\n            <div><b>Streamlit · Plotly</b><p>Интерактивное рабочее пространство</p></div>\n            <div><b>MariaDB · Parquet</b><p>Общий источник аналитики, аккаунтов и проверок</p></div></div>\n            <footer>Freedom Flow · Прототип команды для HackAlem AI<br>\n            Аналитические роли — гипотезы для проверки, а не доказательство нарушения.</footer></section>'))
