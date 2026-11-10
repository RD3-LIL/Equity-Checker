"""Hand-checked tests for dcf.py."""

from dcf import dcf


def test_zero_growth():
    # FCF 100, no growth, WACC 10%, terminal 0%: TV = 100 / 0.10 = 1000
    ev, pv_fcf, pv_tv, fcfs = dcf(100, growth=0.0, wacc=0.10, terminal=0.0, years=5)
    assert fcfs == [100, 100, 100, 100, 100]
    assert abs(pv_tv - 1000 / 1.10 ** 5) < 0.01
    assert abs(ev - (pv_fcf + pv_tv)) < 0.01


def test_growth_raises_value():
    ev_low, _, _, _ = dcf(100, growth=0.03, wacc=0.09, terminal=0.025, years=5)
    ev_high, _, _, _ = dcf(100, growth=0.07, wacc=0.09, terminal=0.025, years=5)
    assert ev_high > ev_low


def test_higher_wacc_lowers_value():
    ev_low, _, _, _ = dcf(100, growth=0.05, wacc=0.08, terminal=0.025, years=5)
    ev_high, _, _, _ = dcf(100, growth=0.05, wacc=0.10, terminal=0.025, years=5)
    assert ev_low > ev_high


def test_negative_fcf_returns_none():
    assert dcf(-10) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(name, "passed")
    print("all DCF tests passed")
