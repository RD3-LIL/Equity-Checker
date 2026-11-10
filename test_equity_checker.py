"""Hand-checked tests for the equity checker flags."""

from equity_checker import (
    flag_cash_conversion,
    flag_accruals,
    flag_dilution,
    flag_leverage,
)


def test_cash_conversion_green():
    flag, _ = flag_cash_conversion(100, 110)
    assert flag == "GREEN"


def test_cash_conversion_red():
    flag, _ = flag_cash_conversion(100, 50)
    assert flag == "RED"


def test_accruals_red():
    flag, _ = flag_accruals(100, 0, 500)
    assert flag == "RED"


def test_accruals_green():
    flag, _ = flag_accruals(100, 95, 1000)
    assert flag == "GREEN"


def test_dilution_red():
    flag, _ = flag_dilution(100, 110)
    assert flag == "RED"


def test_dilution_buyback_green():
    flag, _ = flag_dilution(100, 98)
    assert flag == "GREEN"


def test_leverage_yellow():
    flag, _ = flag_leverage(70, 100)
    assert flag == "YELLOW"


def test_leverage_green():
    flag, _ = flag_leverage(30, 100)
    assert flag == "GREEN"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(name, "passed")
    print("all tests passed")
