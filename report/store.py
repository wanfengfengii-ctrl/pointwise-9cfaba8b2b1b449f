from dataclasses import asdict, dataclass
from fractions import Fraction
import json
import sqlite3

from exchange.catalog import initialize_catalog
from report.origins import ReportOrigin
from report.receipts import Receipt
from review.endpoints import QuadraticRoot, decode_endpoint, encode_endpoint
from review.minimum import BestInterval
from review.partition import Segment
from review.request import MinimumRequest, ReviewRequest, parse_minimum_request, parse_review_request
from station.rational import rational_from_parts, rational_parts
from station.store import is_rowid


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    report_id: int
    request: ReviewRequest
    origin: ReportOrigin
    segments: tuple[Segment, ...]
    receipt: Receipt


@dataclass(frozen=True, slots=True)
class MinimumSnapshot:
    report_id: int
    request: MinimumRequest
    origin: ReportOrigin
    receipt: Receipt
    infimum: Fraction | QuadraticRoot | None
    status: str
    best: tuple[BestInterval, ...]


def initialize_reports(db):
    with db:
        db.executescript('''
            BEGIN;
            CREATE TABLE IF NOT EXISTS reports (
                report_id INTEGER PRIMARY KEY,
                request_json TEXT NOT NULL,
                origin_json TEXT NOT NULL,
                segments_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS report_receipts (
                report_id INTEGER PRIMARY KEY REFERENCES reports(report_id),
                issue_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS minimum_reports (
                report_id INTEGER PRIMARY KEY,
                request_json TEXT NOT NULL,
                origin_json TEXT NOT NULL,
                result_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS minimum_receipts (
                report_id INTEGER PRIMARY KEY REFERENCES minimum_reports(report_id),
                issue_id INTEGER NOT NULL
            );
            COMMIT;
        ''')


def open_center_store(path):
    db = sqlite3.connect(path)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        initialize_catalog(db)
        initialize_reports(db)
    except BaseException:
        db.close()
        raise
    return db


def encode_request(request):
    return {'admission_id': request.admission_id, 'left': rational_parts(request.left),
            'right': rational_parts(request.right), 'tolerance': rational_parts(request.tolerance)}


def _decode_request(data):
    return parse_review_request(data['admission_id'], rational_from_parts(data['left']),
                                rational_from_parts(data['right']),
                                rational_from_parts(data['tolerance']))


def encode_segments(segments):
    return [dict(category=segment.category, left=encode_endpoint(segment.left),
                 right=encode_endpoint(segment.right), left_closed=segment.left_closed,
                 right_closed=segment.right_closed) for segment in segments]


def _decode_segment(data):
    return Segment(decode_endpoint(data['left']), decode_endpoint(data['right']),
                   data['left_closed'], data['right_closed'], data['category'])


def encode_minimum_request(request):
    return {'admission_id': request.admission_id, 'left': rational_parts(request.left),
            'right': rational_parts(request.right)}


def _decode_minimum_request(data):
    return parse_minimum_request(data['admission_id'], rational_from_parts(data['left']),
                                 rational_from_parts(data['right']))


def encode_minimum_result(status, infimum, best):
    return {'status': status,
            'infimum': None if infimum is None else encode_endpoint(infimum),
            'best': [dict(left=encode_endpoint(interval.left),
                          right=encode_endpoint(interval.right),
                          left_closed=interval.left_closed,
                          right_closed=interval.right_closed) for interval in best]}


def _decode_minimum_result(data):
    infimum = data['infimum']
    best = tuple(BestInterval(decode_endpoint(item['left']), decode_endpoint(item['right']),
                              item['left_closed'], item['right_closed'])
                 for item in data['best'])
    return data['status'], (None if infimum is None else decode_endpoint(infimum)), best


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


class ReportStore:
    def __init__(self, db):
        self.db = db

    def insert_report(self, request, origin, segments):
        cursor = self.db.execute('''
            INSERT INTO reports (request_json, origin_json, segments_json) VALUES (?, ?, ?)
        ''', (_json(encode_request(request)), _json(asdict(origin)), _json(encode_segments(segments))))
        return cursor.lastrowid

    def insert_receipt(self, receipt):
        self.db.execute('INSERT INTO report_receipts (report_id, issue_id) VALUES (?, ?)',
                        (receipt.report_id, receipt.issue_id))

    def fetch_report(self, report_id):
        if not is_rowid(report_id):
            raise ValueError('invalid report id')
        row = self.db.execute('''
            SELECT r.request_json, r.origin_json, r.segments_json, s.issue_id
            FROM reports AS r JOIN report_receipts AS s USING (report_id) WHERE report_id = ?
        ''', (report_id,)).fetchone()
        if row is None:
            return None
        request_json, origin_json, segments_json, parent_issue = row
        return ReportSnapshot(report_id, _decode_request(json.loads(request_json)),
                              ReportOrigin(**json.loads(origin_json)),
                              tuple(map(_decode_segment, json.loads(segments_json))),
                              Receipt(report_id, parent_issue))

    def insert_minimum(self, request, origin, status, infimum, best):
        cursor = self.db.execute('''
            INSERT INTO minimum_reports (request_json, origin_json, result_json) VALUES (?, ?, ?)
        ''', (_json(encode_minimum_request(request)), _json(asdict(origin)),
              _json(encode_minimum_result(status, infimum, best))))
        return cursor.lastrowid

    def insert_minimum_receipt(self, receipt):
        self.db.execute('INSERT INTO minimum_receipts (report_id, issue_id) VALUES (?, ?)',
                        (receipt.report_id, receipt.issue_id))

    def fetch_minimum(self, report_id):
        if not is_rowid(report_id):
            raise ValueError('invalid report id')
        row = self.db.execute('''
            SELECT r.request_json, r.origin_json, r.result_json, s.issue_id
            FROM minimum_reports AS r JOIN minimum_receipts AS s USING (report_id)
            WHERE report_id = ?
        ''', (report_id,)).fetchone()
        if row is None:
            return None
        request_json, origin_json, result_json, parent_issue = row
        status, infimum, best = _decode_minimum_result(json.loads(result_json))
        return MinimumSnapshot(report_id, _decode_minimum_request(json.loads(request_json)),
                               ReportOrigin(**json.loads(origin_json)),
                               Receipt(report_id, parent_issue), infimum, status, best)
