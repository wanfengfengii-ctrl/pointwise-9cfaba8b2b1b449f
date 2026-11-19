"""Infimum of the absolute channel difference over a closed setpoint window.

With affine channel parts the difference is f(x) = P(x)/Q(x) where
P = N_first*D_second - N_second*D_first and Q = D_first*D_second are
polynomials of degree at most two.  Setpoints where an original
denominator vanishes are unreadable holes, even when f has a finite
limit there.  The infimum over the readable points of the window is
witnessed by readable window endpoints, zeros of P, zeros of the
derivative numerator R = P'Q - PQ', or finite limits at the holes.
"""
from dataclasses import dataclass
from fractions import Fraction
from functools import cmp_to_key
from math import gcd, lcm

from calibration.evidence import CertificateEvidence
from review.algebra import real_roots
from review.endpoints import Endpoint, QuadraticRoot, compare_endpoints
from review.request import MinimumRequest


@dataclass(frozen=True, slots=True)
class BestInterval:
    left: Endpoint
    right: Endpoint
    left_closed: bool
    right_closed: bool


def _multiply(first, second):
    return (first.intercept * second.intercept,
            first.slope * second.intercept + first.intercept * second.slope,
            first.slope * second.slope)


def _poly_at(poly, point):
    constant, linear, quadratic = poly
    return (quadratic * point + linear) * point + constant


def _constant_ratio(P, Q):
    """Return c when P == c*Q as polynomials (f is constant), else None."""
    for p, q in zip(P, Q):
        if q:
            ratio = p / q
            if all(a == ratio * b for a, b in zip(P, Q)):
                return ratio
            return None
    return None  # unreachable: Q is never identically zero


def _derivative_numerator(P, Q):
    p0, p1, p2 = P
    q0, q1, q2 = Q
    return (p1 * q0 - p0 * q1, 2 * (p2 * q0 - p0 * q2), p2 * q1 - p1 * q2)


def _in_window(point, left, right):
    return (compare_endpoints(left, point) <= 0
            and compare_endpoints(point, right) <= 0)


def _deflate(poly, hole):
    constant, linear, quadratic = poly
    return (linear + quadratic * hole, quadratic, Fraction(0))


def _multiplicity(poly, hole):
    poly = tuple(poly)
    multiplicity = 0
    while any(poly) and _poly_at(poly, hole) == 0:
        poly = _deflate(poly, hole)
        multiplicity += 1
    return multiplicity, poly


def _hole_limit(P, Q, hole):
    """Limit of |P/Q| towards a hole, or None when it is unbounded."""
    j, remaining_p = _multiplicity(P, hole)
    k, remaining_q = _multiplicity(Q, hole)
    if j > k:
        return Fraction(0)
    if j < k:
        return None
    return abs(_poly_at(remaining_p, hole) / _poly_at(remaining_q, hole))


def _root_value(P, Q, root):
    """Return (u, v) with P(root)/Q(root) == u + v*root for a QuadraticRoot."""
    a, b, c = Fraction(root.a), Fraction(root.b), Fraction(root.c)
    p0, p1, p2 = P
    q0, q1, q2 = Q
    u_p, v_p = p0 - p2 * c / a, p1 - p2 * b / a
    u_q, v_q = q0 - q2 * c / a, q1 - q2 * b / a
    norm = u_q * u_q - u_q * v_q * b / a + v_q * v_q * c / a
    u = (u_p * u_q + v_p * v_q * c / a - u_p * v_q * b / a) / norm
    v = (u_q * v_p - u_p * v_q) / norm
    return u, v


def _encode_root_value(u, v, root):
    """Return |u + v*root| as a Fraction or a QuadraticRoot."""
    if v == 0:
        return abs(u)
    sign = compare_endpoints(root, -u / v)
    if v < 0:
        sign = -sign
    w, t = (u, v) if sign > 0 else (-u, -v)
    a, b = root.a, root.b
    m = 2 * a * w - b * t
    n = root.branch * t
    # |u + v*root| == (m + n*sqrt(discriminant)) / (2a); build its primitive polynomial.
    leading = 4 * a * a
    middle = -4 * a * m
    tail = m * m - n * n * root.discriminant
    scale = lcm(leading.denominator, middle.denominator, tail.denominator)
    ai, bi, ci = (int(term * scale) for term in (leading, middle, tail))
    common = gcd(ai, bi, ci)
    if ai < 0:
        common = -common
    return QuadraticRoot(ai // common, bi // common, ci // common, 1 if n > 0 else -1)


def _readable_intervals(left, right, holes):
    intervals = []
    anchor, anchor_closed = left, left not in holes
    for hole in holes:
        if anchor < hole:
            intervals.append(BestInterval(anchor, hole, anchor_closed, False))
        anchor, anchor_closed = hole, False
    if anchor < right or anchor_closed:
        intervals.append(BestInterval(anchor, right, anchor_closed, True))
    return tuple(intervals)


def minimum_result(evidence: CertificateEvidence, request: MinimumRequest):
    """Return (status, infimum, best) for the window of the request."""
    left, right = request.left, request.right
    first, second = evidence.responses
    P = tuple(a - b for a, b in zip(_multiply(first.numerator, second.denominator),
                                    _multiply(second.numerator, first.denominator)))
    Q = _multiply(first.denominator, second.denominator)
    holes = tuple(hole for hole in evidence.unreadable_setpoints if left <= hole <= right)
    if left == right and holes:
        return 'empty', None, ()
    ratio = _constant_ratio(P, Q)
    if ratio is not None:
        return 'attained', abs(ratio), _readable_intervals(left, right, holes)
    hole_set = set(holes)
    candidates = []
    for endpoint in sorted({left, right} - hole_set):
        candidates.append((abs(_poly_at(P, endpoint) / _poly_at(Q, endpoint)), True))
    for root in real_roots(P):
        if root not in hole_set and _in_window(root, left, right):
            candidates.append((Fraction(0), True))
    stationary = []
    for root in real_roots(_derivative_numerator(P, Q)):
        if root not in hole_set and _in_window(root, left, right):
            if isinstance(root, Fraction):
                value = abs(_poly_at(P, root) / _poly_at(Q, root))
            else:
                value = _encode_root_value(*_root_value(P, Q, root), root)
            stationary.append((root, value))
            candidates.append((value, True))
    for hole in holes:
        limit = _hole_limit(P, Q, hole)
        if limit is not None:
            candidates.append((limit, False))
    infimum, attained = candidates[0]
    for value, is_attained in candidates[1:]:
        order = compare_endpoints(value, infimum)
        if order < 0:
            infimum, attained = value, is_attained
        elif order == 0:
            attained = attained or is_attained
    if not attained:
        return 'unattained', infimum, ()
    if isinstance(infimum, Fraction):
        points = set()
        for sign in (1, -1):
            shifted = tuple(p - sign * infimum * q for p, q in zip(P, Q))
            points.update(root for root in real_roots(shifted)
                          if root not in hole_set and _in_window(root, left, right))
    else:
        points = {root for root, value in stationary
                  if compare_endpoints(value, infimum) == 0}
    ordered = sorted(points, key=cmp_to_key(compare_endpoints))
    return 'attained', infimum, tuple(BestInterval(point, point, True, True)
                                      for point in ordered)
