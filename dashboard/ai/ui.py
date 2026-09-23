"""Button-triggered Streamlit fragment. Never starts external work on rerun."""
from dataclasses import replace
import time
import pandas as pd
import streamlit as st
from dashboard.i18n import t, html_t, translator, language
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
            st.caption(t('Исходных строк: {v0}. Индексы от нуля в файлах указанной версии. На экране первые 200.', v0=f'{len(sources)}'))
            if sources:
                st.dataframe(pd.json_normalize(sources[:200]), hide_index=True, width='stretch')

def show_result(result):
    if result.get('cache_warning'):
        st.caption(t(result['cache_warning']))
    st.caption(t('Модель: {v0} · версия {v1} · исходное время {v2} с · кеш: {v3}', v0=f"{result['model']}", v1=f"{result['data_hash'][:16]}", v2=f"{result['seconds']:.2f}", v3=t('да' if result['cache_hit'] else 'нет')))
    if result['cache_hit']:
        st.caption(t('Получение из кеша: {v0} с; новый API-запрос не выполнялся.', v0=f"{result.get('lookup_seconds', 0):.3f}"))
    if result.get('usage'):
        st.caption(t('Токены, сообщённые API: ') + canonical(result['usage']) + t(' (сумма ответов с usage).'))
    if result['ok']:
        st.subheader(t('Интерпретация AI'))
        st.text(result['answer']['summary'])
        st.caption(t('Интерпретация модели не является установленным фактом. Схема JSON не гарантирует достоверность текста.'))
        for h in result['answer']['hypotheses']:
            with st.container(border=True):
                st.text(h['description'])
                st.badge(t(h['status']), color='green' if h['status'] == 'поддерживается наблюдениями' else 'orange')
                for label, refs in [('Подтверждающие наблюдения', h['supporting']), ('Противоречащие наблюдения', h['contradicting'])]:
                    st.markdown('**' + t(label) + '**')
                    if not refs:
                        st.caption(t('Не приведены. Это не означает отсутствия других объяснений.'))
                    for ref in refs:
                        record = result['evidence']['records'][ref['evidence_id']]
                        st.text(f"{ref['metric']}: {canonical(record['values'][ref['metric']])}")
                        st.markdown(t('[Источник: {v0}](#{v1})', v0=f"{ref['evidence_id']}", v1=f"{ref['evidence_id']}"))
                for alternative in h['alternatives']:
                    st.text(t('Предположение: ') + alternative)
                st.text(t('Недостающие данные: ') + '; '.join(h['missing_data']))
                st.text(t('Следующий шаг: ') + h['next_step'])
                st.caption(t('Выполненные проверки: ') + ', '.join(h['checks']))
    else:
        st.warning(t(result.get('error', 'AI-вывод не сформирован.')))
    st.subheader(t('Вычисленные факты и источники'))
    show_evidence(result['evidence'])
    with st.expander(t('Журнал инструментальных проверок')):
        st.json(result['checks'])
    st.subheader(t('Ограничения данных'))
    for text in result['limitations']:
        st.caption(t(text))
    st.download_button(t('Скачать AI-записку в Markdown'), markdown_report(result), f"investigation_{result['gid']}.md", 'text/markdown', key='ai_download')
    st.download_button(t('Скачать реестр доказательств'), canonical(result['evidence']), f"evidence_{result['gid']}.json", 'application/json', key='ai_evidence_download')

def translate_progress(message):
    for prefix in ('Ищу противоречия: ', 'Проверяю гипотезу: '):
        if message.startswith(prefix):
            return t(prefix) + message[len(prefix):]
    return t(message)

@st.fragment
def investigation_panel(gid, files, store, user_id, dataset_id, queue=None, show_title=True):
    queue_key = f'{user_id}:{dataset_id}:{language()}'
    completed = st.session_state.get('_ai_completed', {}).get(queue_key, [])
    if queue is not None:
        pending = [value for value in queue if value not in completed]
        queue_progress = st.empty()
        queue_next = st.empty()
        queue_progress.caption(t('Проверено в этой сессии: {count} из {total}', count=len(completed), total=len(queue)))
        if pending:
            gid = pending[0]
            queue_next.caption(t('Следующая цепочка: окружение участника {gid}', gid=gid))
        else:
            st.success(t('Очередь завершена. Можно выбрать участника для повторной проверки.'))

    if show_title:
        st.subheader(t('AI-расследователь'), icon=':material/travel_explore:')
    st.caption(t('По кнопке профиль и ограниченное окружение клиента передаются во внешний API. Весь датасет, пароли, заметки и данные вашего аккаунта не передаются.'))
    settings, config_error = (None, None)
    try:
        settings = replace(Settings.from_env(), language=language())
        settings.validate()
    except AIError as error:
        config_error = str(error)
    if config_error:
        st.info(t(config_error))
    if settings and (not config_error):
        st.caption(t('Провайдер: {v0} · модель: {v1}', v0=f'{settings.base_url}', v1=f'{settings.model}'))
    consent = st.checkbox(t('Разрешаю отправлять выбранные цепочки во внешний AI API по моему нажатию' if queue is not None else 'Разрешаю отправить фрагмент этого кейса во внешний AI API'), key='ai_queue_consent' if queue is not None else f'ai_consent_{gid}')
    run = st.button(t('Расследовать следующую цепочку' if queue is not None else 'AI-расследование'), key='ai_run', type='primary', disabled=bool(config_error) or not consent or (queue is not None and not pending))
    scope = f'{user_id}:{dataset_id}:{gid}'
    if run:
        started = time.monotonic()
        with st.status(t('Готовлю расследование'), expanded=True) as status:
            try:
                data = case_data(files)
                result = investigate(data, gid, settings, ResultCache(store, user_id), progress=lambda message: status.write(translate_progress(message)), started=started)
                st.session_state['_ai_result'] = (scope, settings.cache_key(data.version, gid), result)
                if result['ok'] and queue is not None:
                    progress_map = dict(st.session_state.get('_ai_completed', {}))
                    progress_map[queue_key] = completed + [gid]
                    st.session_state['_ai_completed'] = progress_map
                    queue_progress.caption(t('Проверено в этой сессии: {count} из {total}', count=len(completed) + 1, total=len(queue)))
                    if len(pending) > 1:
                        queue_next.caption(t('Следующая цепочка: окружение участника {gid}', gid=pending[1]))
                    else:
                        queue_next.success(t('Очередь завершена. Можно выбрать участника для повторной проверки.'))
                status.update(label=t('Расследование завершено' if result['ok'] else 'Доступны факты; AI-вывод не сформирован'), state='complete' if result['ok'] else 'error', expanded=False)
            except Exception:
                status.update(label=t('Не удалось подготовить данные для расследования'), state='error')
                st.error(t('Проверьте исходные файлы и конфигурацию AI на сервере.'))
    saved = st.session_state.get('_ai_result')
    if saved and (saved[0] == scope or (queue is not None and saved[0] == f"{user_id}:{dataset_id}:{saved[2]['gid']}")) and settings and saved[1] == settings.cache_key(saved[2]['data_hash'], saved[2]['gid']):
        st.caption('gid ' + str(saved[2]['gid']))
        show_result(saved[2])
        if queue is not None:
            gid = saved[2]['gid']
            scope = saved[0]
    if st.button(t('Что изменится без этого участника?'), key='ai_remove'):
        try:
            with st.spinner(t('Проверяю копию наблюдаемого графа')):
                tools = Toolset(case_data(files), gid, max_calls=1)
                tools.execute('simulate_node_removal', {'gid': gid})
                st.session_state['_ai_removal'] = (scope, tools.registry.export())
        except Exception:
            st.error(t('Не удалось выполнить локальную симуляцию. Проверьте исходные данные.'))
    removal = st.session_state.get('_ai_removal')
    if removal and removal[0] == scope:
        st.caption(t('Локальная структурная проверка, без API. Она не доказывает экономический эффект, контроль или прекращение переводов.'))
        show_evidence(removal[1])
