"""Public welcome screen and browser persistence for revocable session tokens."""
from pathlib import Path
import streamlit as st
import streamlit.components.v2 as components

ASSETS = Path(__file__).parent / 'frontend'


def _session_component():
    return components.component('moneygraph_browser_session', html='<span></span>', js='''
export default function({data, parentElement, setStateValue}) {
    const marker = parentElement.querySelector('span');
    const publish = (token, error) => {
        const signature = JSON.stringify([token, error]);
        if (marker.dataset.result === signature) return;
        marker.dataset.result = signature;
        setStateValue('token', token);
        setStateValue('error', error);
    };
    const key = 'moneygraph.session.v1';
    try {
        if (data.action === 'clear') localStorage.removeItem(key);
        if (data.action === 'save') localStorage.setItem(key, JSON.stringify({
            token: data.token, expires: Date.now() + 30 * 86400000
        }));
        let token = null;
        if (data.action === 'read') {
            const saved = JSON.parse(localStorage.getItem(key) || 'null');
            if (saved && saved.expires > Date.now() && typeof saved.token === 'string') token = saved.token;
            else localStorage.removeItem(key);
        }
        publish(token, false);
    } catch (_) {
        publish(null, true);
    }
}
''')


def browser_session():
    result = _session_component()(key='browser_session',
        data=st.session_state.get('_browser_action', {'action': 'read'}),
        default={'token': None, 'error': False},
        on_token_change=lambda: None, on_error_change=lambda: None, height=0)
    if result.error:
        st.caption('Браузер запретил сохранение входа. В этой вкладке вход работает как обычно.')
    return result


def _open(mode):
    st.session_state['_auth_page'] = True
    st.session_state['auth_mode'] = mode


def landing():
    with st.container(key='welcome'):
        st.html('''<div class="mg-welcome-head"><span>◈ &nbsp; MoneyGraph</span>
            <small>ГРАФ ДЕНЕГ · HACKALEM AI</small></div>
            <div class="mg-welcome-copy"><span class="mg-login-tag">ОТ ПЕРЕВОДА К ПОЛНОЙ КАРТИНЕ</span>
            <h1>У каждого перевода<br>есть продолжение.</h1>
            <p>Увидьте цепочки движения денег, исследуйте связи<br>
            и соберите наблюдения в личном пространстве аналитика.</p></div>''')
        with st.container(key='welcome_actions', horizontal=True, horizontal_alignment='center'):
            st.button('Войти', key='welcome_login', type='primary', icon=':material/arrow_forward:', on_click=_open, args=('Вход',))
            st.button('Создать аккаунт', key='welcome_register', on_click=_open, args=('Регистрация',))
        hero = ASSETS / 'chain-hero.png'
        if hero.exists():
            st.image(str(hero), width='stretch')
        st.html('''<div class="mg-welcome-features">
            <div><small>01 / ИССЛЕДУЙТЕ</small><h3>Связи в одной картине</h3><p>Направленный граф помогает проследить наблюдаемые переводы.</p></div>
            <div><small>02 / ПРОВЕРЯЙТЕ</small><h3>Числа за каждым выводом</h3><p>Роли и приоритеты сопровождаются наблюдениями и ограничениями данных.</p></div>
            <div><small>03 / СОХРАНЯЙТЕ</small><h3>Своё пространство</h3><p>Личные заметки и проверки доступны после следующего входа.</p></div></div>
            <section class="mg-stack"><span class="mg-login-tag">ПОД КАПОТОМ</span>
            <h2>Прозрачная аналитика. Понятные инструменты.</h2>
            <div class="mg-stack-grid">
            <div><b>Python · pandas · NumPy</b><p>Подготовка данных и расчёт признаков</p></div>
            <div><b>NetworkX</b><p>Граф, центральности и кластеры связей</p></div>
            <div><b>Streamlit · Plotly</b><p>Интерактивное рабочее пространство</p></div>
            <div><b>SQLite · Parquet</b><p>Аккаунты, сохранённые проверки и данные</p></div></div>
            <footer>MoneyGraph · Прототип команды для HackAlem AI<br>
            Аналитические роли — гипотезы для проверки, а не доказательство нарушения.</footer></section>''')
