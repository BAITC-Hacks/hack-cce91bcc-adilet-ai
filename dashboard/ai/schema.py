"""Structural and semantic reference checks; NOT a truth oracle for free text."""
import re
from decimal import Decimal
from jsonschema import Draft202012Validator
from dashboard.ai.config import AIError
from dashboard.ai.evidence import canonical
from dashboard.ai.tools import obj, TOOL_SCHEMAS

TEXT = {'type':'string','minLength':1,'maxLength':500}
HID = {'type':'string','pattern':'^h[1-3]$'}
CHECK_ID = {'type':'string','pattern':'^check_[0-9]+$'}

def array(items, low=0, high=8):
    return {'type':'array','items':items,'minItems':low,'maxItems':high}

TOOL_CALL = {'oneOf':[obj({'name':{'const':name},'arguments':schema}) for name,schema in TOOL_SCHEMAS.items()]}
PLAN_SCHEMA = obj({'hypotheses':array(obj({'hypothesis_id':HID,'description':TEXT}),1,3),
    'checks':array(obj({'hypothesis_id':HID,'purpose':{'enum':['support','challenge']},'tool':TOOL_CALL}),2,11)})
REFERENCE = obj({'evidence_id':{'type':'string','pattern':'^ev_[a-f0-9]{20}$'},
    'gid':{'type':'string','pattern':'^[0-9]+$'},
    'date_from':{'type':'string'},'date_to':{'type':'string'},'metric':{'type':'string'},
    'value':{'type':['string','number','boolean']}})
STATUSES = ['поддерживается наблюдениями','есть противоречия','недостаточно данных']
FINAL_SCHEMA = obj({'summary':TEXT,'hypotheses':array(obj({
    'hypothesis_id':HID,'description':TEXT,'status':{'enum':STATUSES},
    'supporting':array(REFERENCE,0,6),'contradicting':array(REFERENCE,0,6),
    'alternatives':array(TEXT,1,3),'missing_data':array(TEXT,1,4),
    'checks':array(CHECK_ID,1,12),'next_step':TEXT}),1,3)})


def structure(value, schema):
    if list(Draft202012Validator(schema).iter_errors(value)):
        raise AIError('Ответ AI не соответствует обязательной структуре.')


def prose(value):
    # Numbers belong only in checked references; inference is displayed separately.
    if re.search(r'\d|https?://|<[^>]+>', value):
        raise AIError('Числа, ссылки и разметка недопустимы в интерпретации: используйте доказательства.')
    if re.search(r'преступник|доказан\w* мошеннич|доказан\w* правонаруш|виновен|criminal|proven fraud|fraud is proven|guilty|қылмыскер|кінәлі|алаяқтық дәлелден', value, re.I):
        raise AIError('Вывод содержит недопустимое утверждение о правонарушении.')


def validate_plan(plan, budget):
    structure(plan, PLAN_SCHEMA)
    ids = {h['hypothesis_id'] for h in plan['hypotheses']}
    if len(ids) != len(plan['hypotheses']) or len(plan['checks']) > budget:
        raise AIError('План превышает бюджет или повторяет гипотезы.')
    for h in plan['hypotheses']:
        prose(h['description'])
        checks = [c for c in plan['checks'] if c['hypothesis_id']==h['hypothesis_id']]
        if {c['purpose'] for c in checks} != {'support','challenge'}:
            raise AIError('Для каждой гипотезы нужны поддерживающая проверка и поиск противоречий.')
        if len({canonical(c['tool']) for c in checks}) < 2:
            raise AIError('Поиск противоречий должен отличаться от поддерживающей проверки.')
    if any(c['hypothesis_id'] not in ids for c in plan['checks']):
        raise AIError('Проверка ссылается на неизвестную гипотезу.')
    return plan


def validate_final(answer, registry, calls, plan):
    structure(answer, FINAL_SCHEMA)
    prose(answer['summary'])
    expected = {h['hypothesis_id'] for h in plan['hypotheses']}
    if {h['hypothesis_id'] for h in answer['hypotheses']} != expected or len(answer['hypotheses']) != len(expected):
        raise AIError('Итог не соответствует запланированным гипотезам.')
    trace = {c['check_id']:c for c in calls}
    for h in answer['hypotheses']:
        for text in [h['description'],h['next_step'],*h['alternatives'],*h['missing_data']]:
            prose(text)
        relevant = [c for c in calls if c.get('hypothesis_id') == h['hypothesis_id']]
        if set(h['checks']) != {c['check_id'] for c in relevant}:
            raise AIError('В итог включены невыполненные или пропущенные проверки.')
        failed = any(not c['ok'] for c in relevant)
        if failed and h['status'] != 'недостаточно данных':
            raise AIError('При невыполненной проверке допустим только статус недостаточности данных.')
        if h['contradicting'] and not failed and h['status'] != 'есть противоречия':
            raise AIError('Статус не учитывает перечисленные противоречия.')
        if h['status']=='есть противоречия' and not h['contradicting']:
            raise AIError('Для статуса противоречия нужны наблюдения.')
        if h['status']=='поддерживается наблюдениями' and not h['supporting']:
            raise AIError('Нет подтверждающих наблюдений.')
        allowed_evidence = {e for c in relevant if c['ok'] for e in c['evidence_ids']}
        allowed_evidence.update(calls[0].get('evidence_ids',[]))
        for ref in h['supporting']+h['contradicting']:
            record = registry.records.get(ref['evidence_id'])
            if record is None or ref['evidence_id'] not in allowed_evidence:
                raise AIError('Неизвестное или не относящееся к проверкам доказательство.')
            if ref['gid'] not in record['gids'] or {k:ref[k] for k in ('date_from','date_to')} != record['period']:
                raise AIError('Доказательство не соответствует gid или периоду.')
            actual = record['values'].get(ref['metric'])
            expected = ref['value']
            numeric = type(actual) in (int,float) and type(expected) in (int,float)
            equal = Decimal(str(actual)) == Decimal(str(expected)) if numeric else canonical(actual) == canonical(expected)
            if ref['metric'] not in record['values'] or not equal:
                raise AIError('Значение утверждения не совпадает с вычисленным доказательством.')
    return answer
