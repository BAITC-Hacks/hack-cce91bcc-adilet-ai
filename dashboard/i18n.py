"""Local UI translations. Dataset text and user notes retain their original language."""
import csv
from functools import lru_cache, partial
from html import escape
from pathlib import Path
import re
import streamlit as st

LANGUAGES = {'ru': 'RU · Русский', 'en': 'EN · English', 'kk': 'KZ · Қазақша'}


def language():
    value = st.session_state.get('language', 'ru')
    return value if value in LANGUAGES else 'ru'


@lru_cache(maxsize=1)
def catalog():
    with (Path(__file__).parent / 'locales.tsv').open(encoding='utf-8') as source:
        return {row[0]: {'en': row[1], 'kk': row[2]} for row in csv.reader(source, delimiter='\t') if len(row) == 3}


def translate(text, locale, **values):
    if text is None:
        return None
    text = str(text)
    translated = catalog().get(text, {}).get(locale, text)
    return translated.format(**values) if values else translated


def t(text, **values):
    return translate(text, language(), **values)


def translator():
    """Capture locale for widget formatters invoked outside the script context."""
    return partial(translate, locale=language())


def html_t(markup):
    # Static markup only. Never alter attributes, CSS, IDs, or supplied evidence.
    if markup.lstrip().startswith('<style>'):
        return markup
    def translate(match):
        value = match.group(1)
        stripped = value.strip()
        translated = t(stripped)
        if translated == stripped:
            return match.group(0)
        return '>' + value.replace(stripped, escape(translated)) + '<'
    return re.sub(r'>([^<>]+)<', translate, markup)


def language_picker():
    st.selectbox('Language / Язык / Тіл', list(LANGUAGES), format_func=LANGUAGES.get,
                 key='language', label_visibility='collapsed', bind='query-params')
