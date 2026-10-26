"""Hand computed checks for every index.

c = [10, 8, 5, 4, 3], y = [1, 2, 5, 10, 4], a = [2, 4, 1, 2, 3]
"""

import math
from datetime import date, timedelta

import pytest

from pubtrack import metrics as m

C = [10, 8, 5, 4, 3]
Y = [1, 2, 5, 10, 4]
A = [2, 4, 1, 2, 3]


def test_h():
    assert m.h_index(C) == 4
    assert m.h_index([]) == 0
    assert m.h_index([0, 0]) == 0
    assert m.h_index([100]) == 1
    assert m.h_index([3, 3, 3]) == 3


def test_g():
    # cumulative 10, 18, 23, 27, 30 against 1, 4, 9, 16, 25
    assert m.g_index(C) == 5
    assert m.g_index([1, 1, 1]) == 1
    # Publish or Perish pads with uncited papers, so g can exceed the paper count
    assert m.g_index([100]) == 10
    assert m.g_index([]) == 0
    assert m.g_index([0, 0]) == 0


def test_i10():
    assert m.i10(C) == 1


def test_e():
    # h = 4, top four sum to 27, excess 27 - 16 = 11
    assert m.e_index(C) == pytest.approx(math.sqrt(11))


def test_hc():
    # 4c/y = 40, 16, 4, 1.6, 3 -> sorted 40, 16, 4, 3, 1.6 -> hc = 3
    assert m.hc_index(C, Y) == 3


def test_hi_norm():
    # c/a = 5, 2, 5, 2, 1 -> sorted 5, 5, 2, 2, 1 -> 2
    assert m.hi_norm(C, A) == 2


def test_annual_h():
    # c/y = 10, 4, 1, 0.4, 0.75 -> 2
    assert m.annual_h(C, Y) == 2


def test_awcr():
    assert m.awcr(C, Y) == pytest.approx(16.15)
    assert m.awcr_pa(C, Y, A) == pytest.approx(7.45)


def test_velocity():
    d0 = date(2026, 1, 4)
    dates = [(d0 + timedelta(weeks=i)).isoformat() for i in range(10)]
    vals = [1000 + 7 * i for i in range(10)]  # one citation per day
    assert m.ols_slope_per_year(dates, vals) == pytest.approx(365.25, rel=1e-9)
    assert m.ols_slope_per_year(dates[:1], vals[:1]) is None


def test_hm_and_acc():
    # sorted by c: (10,a2) (8,a4) (5,a1) (4,a2) (3,a3); r_eff 0.5, 0.75, 1.75, 2.25, 2.583 -> all hold -> hm = 2.583
    assert m.hm_index(C, A) == pytest.approx(0.5 + 0.25 + 1 + 0.5 + 1 / 3)
    assert m.hm_index([1, 1, 1], [1, 1, 1]) == 1
    assert [m.acc(C, n) for n in (1, 2, 5, 10, 20)] == [5, 5, 3, 1, 0]


def test_rate_per_paper_and_recent_subset():
    assert m.rate_per_paper(C, Y) == pytest.approx([10, 4, 1, 0.4, 0.75])
    papers = [{"year": 2019}, {"year": 2021}, {"year": 2025}, {"year": None}, {"year": 2026}]
    assert m.window_bounds(2026) == (2021, 2025)
    assert [p["year"] for p in m.recent_subset(papers, 2021, 2025)] == [2021, 2025]
