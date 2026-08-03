"""Where a finished transcript goes.

The single most consequential decision in the app: whether the user's words
reach their cursor, or land in a panel they have to copy from. It lived inline
in a 900-line method and was never tested; the ordering of its branches
carries meaning that is easy to "simplify" away.
"""

from __future__ import annotations

import pytest

from fortunevoice.app import decide_delivery


def test_a_confident_editable_field_gets_typed():
    assert decide_delivery(stale=False, focus_held=True, editable=True) == "type"


def test_unknown_editability_still_types():
    """`None` means Windows would not say — normal in terminals and Electron
    apps, where the user really is typing into a real field. Refusing there
    would refuse in exactly the apps people dictate into most."""
    assert decide_delivery(stale=False, focus_held=True, editable=None) == "type"


def test_a_button_does_not_get_typed_into():
    assert decide_delivery(stale=False, focus_held=True, editable=False) == "noedit"


def test_lost_focus_goes_to_the_panel():
    assert decide_delivery(stale=False, focus_held=False, editable=True) == "focus"


def test_stale_wins_over_everything():
    """Twenty seconds after key-up the user has moved on. Typing then lands in
    whatever they are doing NOW, which is worse than not typing at all — so
    this outranks a perfectly good editable field."""
    assert decide_delivery(stale=True, focus_held=True, editable=True) == "stale"


def test_focus_is_checked_before_editability():
    """If focus moved, the editability answer describes the wrong window, so
    the reason reported must be the focus loss."""
    assert decide_delivery(stale=False, focus_held=False, editable=False) == "focus"


@pytest.mark.parametrize("editable", [True, False, None])
def test_every_route_is_a_known_reason(editable):
    """Each branch must return something the caller can turn into a metrics
    outcome and a translated message — a typo here would surface as a missing
    string in front of the user."""
    from fortunevoice.strings import CATALOGUE

    for stale in (True, False):
        for focus_held in (True, False):
            route = decide_delivery(stale=stale, focus_held=focus_held,
                                    editable=editable)
            assert route in {"type", "stale", "focus", "noedit"}
            if route != "type":
                assert f"hold.{route}" in CATALOGUE
