from fractions import Fraction
import sqlite3

from exchange.catalog import MissingAdmission
from minimum_samples import MARKS, admit_marks
from report.export import export_report
from report.publish import MissingReport, read, read_minimum
from review.endpoints import QuadraticRoot
from review.inspection import issue_minimum, review
from review.minimum import BestInterval
from review.setpoint import point
from tests.fixtures import (CONSTANT, QUALIFIED, STATION, UNREADABLE, ProjectCase,
                            hole_segments, reject_minimum_commit, row_count)


def isolated(endpoint):
    return BestInterval(endpoint, endpoint, True, True)


class TestMinimum(ProjectCase):
    def admit_samples(self):
        admitted = {name: admit_marks(self.station, self.center, marks)
                    for name, marks in MARKS.items()}
        _, original = admitted['hole']
        admitted['corrected'] = admit_marks(self.station, self.center, CONSTANT[:2],
                                            original.evidence.revision_id)
        self.station.close()
        return admitted

    def test_sample_table(self):
        admitted = self.admit_samples()
        small, large = QuadraticRoot(1, 0, -2, -1), QuadraticRoot(1, 0, -2, 1)
        cases = {
            ('hole', 0, 2): ('unattained', Fraction(0), ()),
            ('hole', 1, 1): ('empty', None, ()),
            ('corrected', 1, 1): ('attained', Fraction(0), (isolated(Fraction(1)),)),
            ('zero_tie', -2, 2): ('attained', Fraction(0), (isolated(small), isolated(large))),
            ('positive_tie', -2, 2): ('attained', QuadraticRoot(1, 0, -8, 1),
                                      (isolated(small), isolated(large))),
            ('boundary_tie', -1, 1): ('attained', Fraction(1), (isolated(Fraction(1)),)),
            ('constant_with_hole', 0, 2): ('attained', Fraction(0), (
                BestInterval(Fraction(0), Fraction(1), True, False),
                BestInterval(Fraction(1), Fraction(2), False, True))),
        }
        self.assertEqual(set(MARKS) | {'corrected'}, {name for name, _, _ in cases})
        for (name, left, right), (status, infimum, best) in cases.items():
            with self.subTest(name=name, left=left, right=right):
                admission_id, issued = admitted[name]
                result = issue_minimum(self.center, admission_id, left, right)
                self.assertEqual(result.status, status)
                self.assertEqual(result.infimum, infimum)
                self.assertEqual(result.best, best)
                self.assertEqual(result.request.admission_id, admission_id)
                self.assertEqual((result.request.left, result.request.right),
                                 (Fraction(left), Fraction(right)))
                self.assertEqual(result.origin.admission_id, admission_id)
                self.assertEqual(result.origin.station, STATION)
                self.assertEqual(result.origin.issue_id, issued.issue_id)
                self.assertEqual(result.origin.freeze_id, issued.evidence.freeze_id)
                self.assertEqual(result.origin.revision_id, issued.evidence.revision_id)
                self.assertEqual(result.receipt.report_id, result.report_id)
                self.assertEqual(result.receipt.issue_id, issued.issue_id)
                self.assertEqual(read_minimum(self.center, result.report_id), result)

    def test_constant_with_two_holes(self):
        marks = (('甲', 0, 0, 0), ('甲', 1, 1, 1), ('乙', 0, -1, -1), ('乙', 1, 0, 0))
        admission_id, _ = admit_marks(self.station, self.center, marks)
        self.station.close()
        result = issue_minimum(self.center, admission_id, -1, 2)
        self.assertEqual(result.status, 'attained')
        self.assertEqual(result.infimum, Fraction(0))
        self.assertEqual(result.best, (
            BestInterval(Fraction(-1), Fraction(0), True, False),
            BestInterval(Fraction(0), Fraction(1), False, False),
            BestInterval(Fraction(1), Fraction(2), False, True),
        ))
        self.assertEqual(read_minimum(self.center, result.report_id), result)

    def test_window_edges(self):
        admitted = self.admit_samples()
        admission_id, _ = admitted['hole']
        cases = {
            (0, 0): ('attained', Fraction(1), (isolated(Fraction(0)),)),
            (2, 2): ('attained', Fraction(1), (isolated(Fraction(2)),)),
            (0, 1): ('unattained', Fraction(0), ()),
            (1, 2): ('unattained', Fraction(0), ()),
            (2, 3): ('attained', Fraction(1), (isolated(Fraction(2)),)),
        }
        for (left, right), (status, infimum, best) in cases.items():
            with self.subTest(left=left, right=right):
                result = issue_minimum(self.center, admission_id, left, right)
                self.assertEqual(result.status, status)
                self.assertEqual(result.infimum, infimum)
                self.assertEqual(result.best, best)

    def test_trimmed_irrational_tie(self):
        admitted = self.admit_samples()
        admission_id, _ = admitted['positive_tie']
        infimum = QuadraticRoot(1, 0, -8, 1)
        right_half = issue_minimum(self.center, admission_id, 0, 2)
        self.assertEqual(right_half.status, 'attained')
        self.assertEqual(right_half.infimum, infimum)
        self.assertEqual(right_half.best, (isolated(QuadraticRoot(1, 0, -2, 1)),))
        left_half = issue_minimum(self.center, admission_id, -2, -1)
        self.assertEqual(left_half.status, 'attained')
        self.assertEqual(left_half.infimum, infimum)
        self.assertEqual(left_half.best, (isolated(QuadraticRoot(1, 0, -2, -1)),))

    def test_publish_rollback_and_retry(self):
        admitted = self.admit_samples()
        admission_id, issued = admitted['zero_tie']
        reject_minimum_commit(self.center)
        with self.assertRaises(sqlite3.IntegrityError):
            issue_minimum(self.center, admission_id, -2, 2)
        self.assertEqual(row_count(self.center, 'minimum_reports'), 0)
        self.assertEqual(row_count(self.center, 'minimum_receipts'), 0)
        self.assertEqual(row_count(self.center, 'commit_failures'), 0)
        self.assertFalse(self.center.in_transaction)
        self.center.execute('DROP TRIGGER reject_minimum_commit')
        result = issue_minimum(self.center, admission_id, -2, 2)
        self.assertEqual(result.status, 'attained')
        self.assertEqual(result.infimum, Fraction(0))
        self.assertEqual(result.receipt.issue_id, issued.issue_id)
        self.assertEqual(read_minimum(self.center, result.report_id), result)
        self.assertEqual(row_count(self.center, 'minimum_reports'), 1)
        self.assertEqual(row_count(self.center, 'minimum_receipts'), 1)

    def test_reopen_and_correction(self):
        admitted = self.admit_samples()
        hole_id, hole_issued = admitted['hole']
        corrected_id, corrected_issued = admitted['corrected']
        tie_id, _ = admitted['positive_tie']
        original = issue_minimum(self.center, hole_id, 0, 2)
        corrected = issue_minimum(self.center, corrected_id, 0, 2)
        tie = issue_minimum(self.center, tie_id, -2, 2)
        self.center.close()
        self.center = self.open_center()
        self.assertEqual(read_minimum(self.center, original.report_id), original)
        self.assertEqual(read_minimum(self.center, corrected.report_id), corrected)
        self.assertEqual(read_minimum(self.center, tie.report_id), tie)
        self.assertEqual(original.status, 'unattained')
        self.assertEqual(original.infimum, Fraction(0))
        self.assertEqual(original.best, ())
        self.assertEqual(corrected.status, 'attained')
        self.assertEqual(corrected.infimum, Fraction(0))
        self.assertEqual(corrected.best, (isolated(Fraction(1)),))
        self.assertEqual(tie.infimum, QuadraticRoot(1, 0, -8, 1))
        self.assertEqual(read_minimum(self.center, original.report_id).receipt.issue_id,
                         hole_issued.issue_id)
        self.assertEqual(read_minimum(self.center, corrected.report_id).receipt.issue_id,
                         corrected_issued.issue_id)
        with self.assertRaises(MissingReport):
            read_minimum(self.center, 999)

    def test_invalid_request(self):
        admitted = self.admit_samples()
        admission_id, _ = admitted['hole']
        for args in ((2, 0), (0, 'NaN'), (0, None), (0, 0.5), (True, 1), ('1/0', 2)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                issue_minimum(self.center, admission_id, *args)
        for bad_admission in ('', 'A0', 'not-an-id', None, 1):
            with self.subTest(bad_admission=bad_admission), self.assertRaises(ValueError):
                issue_minimum(self.center, bad_admission, 0, 2)
        with self.assertRaises(MissingAdmission):
            issue_minimum(self.center, 'A999', 0, 2)
        for bad_id in (0, -1, '1', 1.5, None, True):
            with self.subTest(bad_id=bad_id), self.assertRaises(ValueError):
                read_minimum(self.center, bad_id)
        with self.assertRaises(MissingReport):
            read_minimum(self.center, 999)
        self.assertEqual(row_count(self.center, 'minimum_reports'), 0)
        self.assertEqual(row_count(self.center, 'minimum_receipts'), 0)

    def test_v1_reports_unaffected(self):
        admitted = self.admit_samples()
        admission_id, issued = admitted['hole']
        threshold = review(self.center, admission_id, 0, 2, '1/2')
        minimum = issue_minimum(self.center, admission_id, 0, 2)
        self.assertEqual(threshold.segments, hole_segments())
        self.assertEqual(minimum.status, 'unattained')
        self.assertEqual(row_count(self.center, 'reports'), 1)
        self.assertEqual(row_count(self.center, 'report_receipts'), 1)
        self.assertEqual(row_count(self.center, 'minimum_reports'), 1)
        self.assertEqual(row_count(self.center, 'minimum_receipts'), 1)
        self.assertEqual(read(self.center, threshold.report_id), threshold)
        self.assertEqual(read_minimum(self.center, minimum.report_id), minimum)
        self.assertEqual(point(self.center, threshold.report_id, 1), UNREADABLE)
        self.assertEqual(point(self.center, threshold.report_id, '1/2'), QUALIFIED)
        exported = export_report(self.center, threshold.report_id)
        self.assertIn('"tolerance":[1,2]', exported)
        self.assertEqual(read_minimum(self.center, minimum.report_id).receipt.issue_id,
                         issued.issue_id)
