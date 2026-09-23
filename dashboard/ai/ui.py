"""Button-triggered Streamlit fragment. Never starts external work on rerun."""
import time
import pandas as pd
import streamlit as st
from dashboard.ai.config import Settings, AIError
from dashboard.ai.evidence import CaseData, LIMITATIONS, canonical
from dashboard.ai.tools import Toolset
from dashboard.ai.engine import investigate, ResultCache, markdown_report


@st.cache_data(show_spinner=False, max_entries=2)
def case_data(files):
    return CaseData.from_files(files)


def show_evidence(evidence):
    for eid, record in evidence['records'].items():
        with st.expander(f"{eid} · {record['kind']}"):
            st.subheader(eid, anchor=eid)
            st.caption(f"gid: {', '.join(record['gids'])} · {record['period']['date_from']} — {record['period']['date_to']}")
            st.json(record['values'])
            sources = [evidence['source_rows'][s] for s in record['source_ids']]
            st.caption(f'Исходных строк: {len(sources)}. Индексы от нуля в файлах указанной версии. На экране первые 200.')
            if sources:
                st.dataframe(pd.json_normalize(sources[:200]), hide_index=True, width='stretch')


def show_result(result):
    if result.get('cache_warning'):
        st.caption(result['cache_warning'])
    st.caption(f"Модель: {result['model']} · версия {result['data_hash'][:16]} · исходное время {result['seconds']:.2f} с · кеш: {'да' if result['cache_hit'] else 'нет'}")
    if result['cache_hit']:
        st.caption(f"Получение из кеша: {result.get('lookup_seconds',0):.3f} с; новый API-запрос не выполнялся.")
    if result.get('usage'):
        st.caption('Токены, сообщённые API: '+canonical(result['usage'])+' (сумма ответов с usage).')
    if result['ok']:
        st.subheader('Интерпретация AI')
        st.text(result['answer']['summary'])
        st.caption('Интерпретация модели не является установленным фактом. Схема JSON не гарантирует достоверность текста.')
        for h in result['answer']['hypotheses']:
            with st.container(border=True):
                st.text(h['description'])
                st.badge(h['status'], color='green' if h['status']=='поддерживается наблюдениями' else 'orange')
                for label, refs in [('Подтверждающие наблюдения',h['supporting']),('Противоречащие наблюдения',h['contradicting'])]:
                    st.markdown('**'+label+'**')
                    if not refs:
                        st.caption('Не приведены. Это не означает отсутствия других объяснений.')
                    for ref in refs:
                        record = result['evidence']['records'][ref['evidence_id']]
                        st.text(f"{ref['metric']}: {canonical(record['values'][ref['metric']])}")
                        st.markdown(f"[Источник: {ref['evidence_id']}](#{ref['evidence_id']})")
                for alternative in h['alternatives']:
                    st.text('Предположение: '+alternative)
                st.text('Недостающие данные: '+'; '.join(h['missing_data']))
                st.text('Следующий шаг: '+h['next_step'])
                st.caption('Выполненные проверки: '+', '.join(h['checks']))
    else:
        st.warning(result.get('error','AI-вывод не сформирован.'))
    st.subheader('Вычисленные факты и источники')
    show_evidence(result['evidence'])
    with st.expander('Журнал инструментальных проверок'):
        st.json(result['checks'])
    st.subheader('Ограничения данных')
    for text in result['limitations']:
        st.caption(text)
    st.download_button('Скачать AI-записку в Markdown', markdown_report(result),
        f"investigation_{result['gid']}.md", 'text/markdown', key='ai_download')
    st.download_button('Скачать реестр доказательств', canonical(result['evidence']),
        f"evidence_{result['gid']}.json", 'application/json', key='ai_evidence_download')


@st.fragment

def investigation_panel(gid, files, store, user_id, dataset_id):
    st.subheader('AI-расследователь', icon=':material/travel_explore:')
    st.caption('По кнопке профиль и ограниченное окружение клиента передаются во внешний API. Весь датасет, пароли, заметки и данные вашего аккаунта не передаются.')
    settings, config_error = None, None
    try:
        settings = Settings.from_env()
        settings.validate()
    except AIError as error:
        config_error = str(error)
    if config_error:
        st.info(config_error)
    if settings and not config_error:
        st.caption(f'Провайдер: {settings.base_url} · модель: {settings.model}')
    consent = st.checkbox('Разрешаю отправить фрагмент этого кейса во внешний AI API', key=f'ai_consent_{gid}')
    run = st.button('AI-расследование', key='ai_run', type='primary', disabled=bool(config_error) or not consent)
    scope = f'{user_id}:{dataset_id}:{gid}'
    if run:
        started = time.monotonic()
        with st.status('Готовлю расследование', expanded=True) as status:
            try:
                data = case_data(files)
                result = investigate(data, gid, settings, ResultCache(store,user_id),
                    progress=lambda message:status.write(message), started=started)
                st.session_state['_ai_result'] = (scope, settings.cache_key(data.version,gid), result)
                status.update(label='Расследование завершено' if result['ok'] else 'Доступны факты; AI-вывод не сформирован', state='complete' if result['ok'] else 'error', expanded=False)
            except Exception:
                status.update(label='Не удалось подготовить данные для расследования', state='error')
                st.error('Проверьте исходные файлы и конфигурацию AI на сервере.')
    saved = st.session_state.get('_ai_result')
    if saved and saved[0]==scope and settings and saved[1]==settings.cache_key(saved[2]['data_hash'],gid):
        show_result(saved[2])
    if st.button('Что изменится без этого участника?', key='ai_remove'):
        try:
            with st.spinner('Проверяю копию наблюдаемого графа'):
                tools = Toolset(case_data(files),gid,max_calls=1)
                tools.execute('simulate_node_removal',{'gid':gid})
                st.session_state['_ai_removal'] = (scope,tools.registry.export())
        except Exception:
            st.error('Не удалось выполнить локальную симуляцию. Проверьте исходные данные.')
    removal = st.session_state.get('_ai_removal')
    if removal and removal[0]==scope:
        st.caption('Локальная структурная проверка, без API. Она не доказывает экономический эффект, контроль или прекращение переводов.')
        show_evidence(removal[1])
