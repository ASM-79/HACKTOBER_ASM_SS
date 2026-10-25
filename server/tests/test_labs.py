"""Every status path of every lab check."""
import pytest

import labs
from labs import LABS


def steps(lab):
    return [s["check"] for s in LABS[lab]["steps"]]


def h(p2s, p3s=None, p1=3300):
    p3s = p3s or [0] * len(p2s)
    return [[p1, a, b, 0] for a, b in zip(p2s, p3s)]


def run(check, v, hist=None):
    hist = hist if hist is not None else [list(v)] * 10
    return check(list(v), hist)


@pytest.mark.parametrize("lab", list(LABS))
def test_rail_step_every_lab(lab):
    rail = steps(lab)[0]
    assert run(rail, [3300, 0, 0, 0])[0] == "ok"
    st, note = run(rail, [0, 0, 0, 0])
    assert st == "waiting" and "target board 3V3" in note
    st, note = run(rail, [1500, 0, 0, 0])
    assert st == "fault" and "share GND" in note


@pytest.mark.parametrize("lab,v", [("divider", [3300, 1650, 0, 0]), ("led", [3300, 1900, 0, 0]),
                                   ("button", [3300, 0, 0, 0]), ("blink", [3300, 0, 0, 0]),
                                   ("button_led", [3300, 0, 0, 0])])
def test_lost_rail_every_step(lab, v):
    for check in steps(lab)[1:]:
        st, note = run(check, [2000] + v[1:])
        assert st == "fault" and "target board 3V3" in note


def test_divider():
    even, onek = steps("divider")[1], steps("divider")[2]
    assert run(even, [3300, 1650, 0, 0])[0] == "ok"
    assert run(onek, [3300, 300, 0, 0])[0] == "ok"
    st, note = run(even, [3300, 3300, 0, 0])
    assert st == "fault" and "R2 is not reaching GND" in note
    st, note = run(even, [3300, 0, 0, 0])
    assert st == "fault" and "R1" in note
    st, note = run(even, [3300, 300, 0, 0])
    assert st == "fault" and "implies R2/R1" in note


def test_led():
    lit, flipped = steps("led")[1], steps("led")[2]
    st, note = run(lit, [3300, 1900, 0, 0])
    assert st == "ok" and "mA" in note
    assert "backwards" in run(lit, [3300, 3300, 0, 0])[1]
    assert "shorted" in run(lit, [3300, 0, 0, 0])[1]
    assert "unusual" in run(lit, [3300, 1200, 0, 0])[1]
    assert run(flipped, [3300, 3300, 0, 0])[0] == "ok"
    assert run(flipped, [3300, 1900, 0, 0])[0] == "waiting"


def test_button():
    released, pressed = steps("button")[1], steps("button")[2]
    assert run(released, [3300, 0, 0, 0])[0] == "ok"
    st, note = run(released, [3300, 900, 0, 0], h([300, 1500, 2600, 400, 1800] * 4))
    assert st == "fault" and "floating" in note
    assert "rotated" in run(released, [3300, 3300, 0, 0])[1]
    st, note = run(released, [3300, 1000, 0, 0])
    assert st == "fault" and "pull-down" in note
    assert run(pressed, [3300, 3300, 0, 0])[0] == "ok"
    assert run(pressed, [3300, 0, 0, 0])[0] == "waiting"
    assert run(pressed, [3300, 1500, 0, 0])[0] == "fault"


BLINK = [0] * 5 + [3300] * 5


def test_blink():
    check = steps("blink")[1]
    assert LABS["blink"]["steps"][1]["starter"]
    assert run(check, [3300, 0, 0, 0], h([0] * 5))[0] == "waiting"
    st, note = run(check, [3300, 0, 0, 0], h(BLINK * 3))
    assert st == "ok" and "blinking" in note
    st, note = run(check, [3300, 0, 0, 0], h([0] * 30))
    assert st == "fault" and "pinMode" in note
    st, note = run(check, [3300, 3300, 0, 0], h([3300] * 30))
    assert st == "fault" and "never goes LOW" in note
    st, note = run(check, [3300, 1600, 0, 0], h([1600] * 30))
    assert st == "fault" and "neither HIGH nor LOW" in note
    assert run(check, [3300, 3300, 0, 0], h([0] * 15 + [3300] * 15))[0] == "waiting"


H, L = 3300, 0


def test_button_led():
    check = steps("button_led")[1]
    assert LABS["button_led"]["steps"][1]["starter"]
    press = [L] * 10 + [H] * 10 + [L] * 10 + [H] * 10
    assert run(check, [3300, 0, 0, 0], h([L] * 5))[0] == "waiting"
    st, note = run(check, [3300, 0, 0, 0], h(press, press))
    assert st == "ok" and "follows the button" in note
    st, note = run(check, [3300, 900, 0, 0], h([300, 1500, 2600, 400, 1800] * 6))
    assert st == "fault" and "floating" in note
    assert run(check, [3300, 0, 0, 0], h([L] * 30))[0] == "waiting"
    st, note = run(check, [3300, 0, 3300, 0], h([L] * 30, [H] * 30))
    assert st == "fault" and "without reading the button" in note
    st, note = run(check, [3300, 0, 0, 0], h(press, [L] * 40))
    assert st == "fault" and "stays at 0 V" in note
    st, note = run(check, [3300, 0, 3300, 0], h(press, [H] * 40))
    assert st == "fault" and "never turns the LED off" in note
    inverted = [H if x == L else L for x in press]
    st, note = run(check, [3300, 0, 0, 0], h(press, inverted))
    assert st == "fault" and "inverted" in note
    flaky = press[:30] + [L] * 10
    st, note = run(check, [3300, 0, 0, 0], h(press, flaky))
    assert st == "fault" and "only sometimes" in note
    held = [H] * 30
    assert run(check, [3300, 3300, 3300, 0], h(held, held))[0] == "waiting"


def test_expected_values_follow_the_real_rail():
    """A sagging 3.0 V rail (seen on a hub-powered clone) still gives correct verdicts and numbers."""
    even, onek = steps("divider")[1], steps("divider")[2]
    st, note = run(even, [3000, 1500, 0, 0])
    assert st == "ok" and "expected 1.50 V" in note
    assert run(onek, [3000, 273, 0, 0])[0] == "ok"
    assert run(even, [3000, 2990, 0, 0])[1].startswith("Midpoint reads the full rail")
    st, note = run(steps("led")[1], [3000, 1900, 0, 0])
    assert st == "ok" and "5.0 mA" in note
    assert run(steps("button")[2], [3000, 2950, 0, 0])[0] == "ok"
    assert run(steps("led")[2], [3000, 2950, 0, 0])[0] == "ok"


def test_instructions_name_target_board_power():
    for lab in LABS.values():
        assert "target board 3V3" in lab["steps"][0]["instruction"]
        for s in lab["steps"]:
            assert "ESP32 3V3" not in s["instruction"]
    assert "Uno" in labs.__doc__ and "3.3 V" in labs.__doc__
