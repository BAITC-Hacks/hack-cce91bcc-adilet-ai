"""Exact input snapshots and stable row identities, including duplicate transfers."""
from hashlib import sha256
from io import BytesIO
import json
import math
import pandas as pd
import networkx as nx
from dashboard.ai.config import AIError

LIMITATIONS = [
    'Все суммы и метрики относятся только к наблюдаемому графу.',
    'Даты без времени суток: операции одного дня не доказывают порядок.',
    'Совместимая по датам цепочка не доказывает движение конкретных денег.',
    'Входящие seed неполны; отсутствие исходящих на глубине четыре может быть границей выборки.',
    'Роль — гипотеза, приоритет — очередность проверки, не вероятность правонарушения.',
    'Пересекающиеся паттерны нельзя складывать без устранения двойного учёта.',
    'Кейс ограничен обходом до четырёх шагов и восьмидесяти узлов, отбор по возрастанию gid; срезы могут быть неполными.',
]


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def scalar(value):
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        raise AIError('Неконечное значение в исходных данных.')
    return value


class CaseData:
    def __init__(self, nodes, edges, tx, version, input_hashes=None):
        self.version = version
        self.input_hashes = input_hashes or {}
        self.nodes, self.edges, self.tx = [f.copy().reset_index(drop=True) for f in (nodes, edges, tx)]
        if len(nodes) > 10000 or len(edges) > 30000 or len(tx) > 100000:
            raise AIError('Набор превышает лимиты локального расследования.')
        for frame, columns in [(self.nodes, ['gid']), (self.edges, ['src', 'dst']), (self.tx, ['src', 'dst'])]:
            for column in columns:
                if not frame[column].astype(str).str.fullmatch(r'\d+').all():
                    raise AIError('Некорректный gid в наборе.')
                frame[column] = frame[column].astype(str)
        if self.nodes.gid.duplicated().any():
            raise AIError('Повторяющийся gid в наборе.')
        self.tx['date'] = pd.to_datetime(self.tx.date, errors='raise').dt.strftime('%Y-%m-%d')
        self.rows = {}
        for filename, frame in [('nodes_roles.csv', self.nodes), ('edges.parquet', self.edges), ('transactions.parquet', self.tx)]:
            ids = []
            for index, record in enumerate(frame.to_dict('records')):
                record = {k: scalar(v) for k, v in record.items()}
                record_id = f'{filename}:{version[:16]}:{index}'
                ids.append(record_id)
                self.rows[record_id] = {'file': filename, 'row_index': index, 'record_id': record_id, 'values': record}
            frame['_record_id'] = ids
        self.period = {'date_from': self.tx.date.min() if len(tx) else '', 'date_to': self.tx.date.max() if len(tx) else ''}
        self.graph = nx.DiGraph()
        self.graph.add_nodes_from(self.nodes.gid)
        for row in self.edges.itertuples():
            self.graph.add_edge(row.src, row.dst, sum_kzt=float(row.sum_kzt))
        if set(self.graph) != set(self.nodes.gid):
            raise AIError('Неизвестный конец ребра.')

    @classmethod
    def from_files(cls, files):
        hashes = {name: sha256(content).hexdigest() for name, content in sorted(files.items())}
        version = sha256(canonical(hashes).encode()).hexdigest()
        nodes = pd.read_csv(BytesIO(files['out/nodes_roles.csv']), dtype={'gid': str})
        edges = pd.read_parquet(BytesIO(files['data/edges.parquet']))
        tx = pd.read_parquet(BytesIO(files['data/transactions.parquet']))
        # Validate source transactions against the complete raw nodes/edges, independently of AI.
        from src.load import validate
        raw_nodes = pd.read_parquet(BytesIO(files['data/nodes.parquet']))
        tx['date'] = pd.to_datetime(tx.date)
        validate(edges, raw_nodes, tx)
        return cls(nodes, edges, tx, version, hashes)


class Registry:
    def __init__(self, data):
        self.data = data
        self.records = {}

    def add(self, kind, gids, values, source_ids, period=None):
        record = {'kind': kind, 'gids': sorted(set(gids)), 'period': period or self.data.period,
            'values': {k: scalar(v) for k, v in values.items()},
            'source_ids': sorted(set(source_ids)), 'data_hash': self.data.version}
        if not all(s in self.data.rows for s in record['source_ids']):
            raise AIError('Неизвестная исходная строка доказательства.')
        evidence_id = 'ev_' + sha256(canonical(record).encode()).hexdigest()[:20]
        record['evidence_id'] = evidence_id
        self.records[evidence_id] = record
        return self.compact(record)

    @staticmethod
    def compact(record):
        return {**record, 'source_ids': record['source_ids'][:8], 'source_count': len(record['source_ids'])}

    def export(self):
        ids = {s for r in self.records.values() for s in r['source_ids']}
        return {'records': self.records, 'source_rows': {s: self.data.rows[s] for s in sorted(ids)},
            'input_hashes': self.data.input_hashes, 'data_hash': self.data.version}
