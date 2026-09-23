"""Email/password UI backed by validated, revocable server-side sessions."""
import streamlit as st
from dashboard.i18n import t, html_t, translator, language
from dashboard.welcome import landing, browser_session
from dashboard.accounts import Accounts, AccountError
from dashboard.ui import brand_html

def clear_session():
    for key in list(st.session_state):
        if key != 'language':
            del st.session_state[key]
    st.session_state['_auth_page'] = True
    st.session_state['_browser_action'] = {'action': 'clear'}

def require_identity(store):
    accounts = Accounts(store)
    browser = browser_session()
    if not st.session_state.get('_auth_token') and browser.token and (not st.session_state.get('_browser_action')):
        candidate = accounts.identity(browser.token)
        if candidate:
            st.session_state['_auth_token'] = browser.token
        else:
            st.session_state['_browser_action'] = {'action': 'clear'}
            st.rerun()
    identity = accounts.identity(st.session_state.get('_auth_token'))
    if identity:
        return identity
    if st.session_state.get('_auth_token'):
        clear_session()
        st.info(t('Сессия завершена. Войдите снова.'))
    if not st.session_state.get('_auth_page'):
        landing()
        st.stop()
    left, right = st.columns([1, 1.15], gap='large')
    with left:
        st.html(html_t('<div class="mg-login">' + brand_html() + '''
            <span class="mg-login-tag">РАБОЧЕЕ ПРОСТРАНСТВО АНАЛИТИКА</span>
            <h1>Весь поток.<br><span>Ясная картина.</span></h1>
            <p>От отдельных переводов — к пониманию связей. Исследуйте денежные потоки и сохраняйте выводы в своём рабочем пространстве.</p>
            <div class="mg-login-grid"><span>◇ &nbsp; Граф переводов</span>
            <span>▤ &nbsp; Личные проверки</span><span>↗ &nbsp; Проверяемые выводы</span></div>
            </div>'''))
    with right, st.container(border=True, key='auth_card'):
        st.html(html_t('<div class="ff-access-label">FREEDOM FLOW / ЛИЧНЫЙ КАБИНЕТ</div>'))
        st.subheader(t('Добро пожаловать'))
        st.caption(t('Войдите в аккаунт или создайте новое рабочее пространство.'))
        if st.button(t('На главную'), icon=':material/arrow_back:', key='auth_back'):
            st.session_state.pop('_auth_page', None)
            st.rerun()
        if st.session_state.get('_recovery_code'):
            st.success(t('Пароль сохранён. Сохраните резервный код перед входом.'))
            st.write(t('Этот код позволит восстановить доступ без почты. Он показывается только сейчас; храните его отдельно от пароля.'))
            st.code(st.session_state['_recovery_code'], language=None)
            st.download_button(t('Скачать резервный код'), st.session_state['_recovery_code'], 'moneygraph-recovery.txt', 'text/plain', icon=':material/download:')
            if st.button(t('Я сохранил код — перейти ко входу'), type='primary', key='recovery_ack'):
                clear_session()
                st.rerun()
            st.stop()
        mode = st.radio(t('Доступ'), ['Вход', 'Регистрация', 'Восстановление'], key='auth_mode', horizontal=True, format_func=translator())
        if mode == 'Вход':
            with st.form('login'):
                email = st.text_input('Email', key='login_email', max_chars=254, autocomplete='username', placeholder='you@example.com')
                password = st.text_input(t('Пароль'), type='password', key='login_password', max_chars=128, autocomplete='current-password')
                remember = st.checkbox(t('Запомнить меня на 30 дней'), help=t('Только на личном устройстве. Пароль не сохраняется.'))
                submitted = st.form_submit_button(t('Войти'), type='primary', width='stretch')
            if submitted:
                try:
                    token = accounts.login(email, password, remember=remember)
                except AccountError as error:
                    st.error(t(str(error)))
                else:
                    clear_session()
                    st.session_state['_auth_token'] = token
                    st.session_state['_browser_action'] = {'action': 'save' if remember else 'clear', 'token': token if remember else ''}
                    st.rerun()
        elif mode == 'Регистрация':
            with st.form('register'):
                st.caption(t('Создайте аккаунт, чтобы сохранять заметки и продолжать проверки с любого устройства.'))
                name = st.text_input(t('Имя'), key='register_name', max_chars=80, autocomplete='name', placeholder=t('Как к вам обращаться'))
                email = st.text_input('Email', key='register_email', max_chars=254, autocomplete='username')
                password = st.text_input(t('Пароль'), type='password', key='register_password', max_chars=128, autocomplete='new-password', help=t('От 15 до 128 символов. Подойдёт длинная фраза.'))
                repeat = st.text_input(t('Повторите пароль'), type='password', key='register_repeat', max_chars=128, autocomplete='new-password')
                submitted = st.form_submit_button(t('Создать аккаунт'), type='primary', width='stretch')
            if submitted:
                try:
                    if password != repeat:
                        raise AccountError('Пароли не совпадают.')
                    recovery = accounts.register(name, email, password)
                except AccountError as error:
                    st.error(t(str(error)))
                else:
                    clear_session()
                    st.session_state['_recovery_code'] = recovery
                    st.rerun()
        else:
            st.caption(t('Используйте резервный код, полученный при регистрации. После восстановления все прежние сессии будут завершены.'))
            with st.form('reset'):
                email = st.text_input('Email', key='reset_email', max_chars=254)
                recovery = st.text_input(t('Резервный код'), type='password', key='reset_code', max_chars=128)
                password = st.text_input(t('Новый пароль'), type='password', key='reset_password', max_chars=128)
                repeat = st.text_input(t('Повторите новый пароль'), type='password', key='reset_repeat', max_chars=128)
                submitted = st.form_submit_button(t('Восстановить доступ'), type='primary', width='stretch')
            if submitted:
                try:
                    if password != repeat:
                        raise AccountError('Пароли не совпадают.')
                    replacement = accounts.reset_password(email, recovery, password)
                except AccountError as error:
                    st.error(t(str(error)))
                else:
                    clear_session()
                    st.session_state['_recovery_code'] = replacement
                    st.rerun()
    st.stop()

def account_controls(store, identity):
    accounts = Accounts(store)
    token = st.session_state['_auth_token']
    with st.popover(''.join(word[0] for word in identity['name'].split()[:2]).upper(), icon=':material/account_circle:', help=t('Аккаунт'), key='profile_menu'):
        st.text(identity['name'])
        st.text(identity['email'])
        if st.button(t('Выйти'), icon=':material/logout:', key='logout', width='stretch'):
            accounts.logout(token)
            clear_session()
            st.rerun()
        with st.expander(t('Безопасность аккаунта'), icon=':material/lock:'):
            with st.form('change_password', clear_on_submit=True):
                old = st.text_input(t('Текущий пароль'), type='password', key='old_password', max_chars=128)
                new = st.text_input(t('Новый пароль'), type='password', key='new_password', max_chars=128)
                repeat = st.text_input(t('Повторите новый пароль'), type='password', key='new_repeat', max_chars=128)
                submitted = st.form_submit_button(t('Сменить пароль'))
            if submitted:
                try:
                    if new != repeat:
                        raise AccountError('Пароли не совпадают.')
                    accounts.change_password(token, old, new)
                except AccountError as error:
                    st.error(t(str(error)))
                else:
                    clear_session()
                    st.rerun()
            if st.button(t('Выйти на всех устройствах'), key='logout_all'):
                accounts.logout(token, all_sessions=True)
                clear_session()
                st.rerun()
