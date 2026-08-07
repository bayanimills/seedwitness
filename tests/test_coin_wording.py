"""A coin is flipped, not rolled, and every screen must say so.

`RollSource` has carried `unit="Flip"` and `prompt="tap the side it landed
on:"` since the coin source was added, with a comment saying the point was to
keep the screens free of per-source special cases. The screens then ignored
both and hardcoded dice wording, so a coin user was told "Rolling Coin",
"tap the value you rolled", "Check Your Rolls", "Rolls used" and
"sha256(rolls)". The vocabulary existed; nothing read it.

Nothing pinned any of that copy, which is why it drifted. These tests pin it
from the user's side: render each coin-facing screen and assert the word
"roll" never reaches the glass, rather than asserting any particular phrasing,
so a future rewrite is free as long as it stays in the user's verb.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402
from seedwitness import entropy as ent  # noqa: E402
from seedwitness import mnemonic as mn  # noqa: E402
from seedwitness.ui import flow_generate as FG  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402


class Recording(MockCanvas):
    """Captures what was actually drawn, not what we think was drawn."""

    def __init__(self):
        super().__init__()
        self.drawn = []

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.drawn.append(str(s))
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)


def _session(source, words=12, rolls=6):
    """`rolls=None` fills the ceremony to completion, which a coin needs: a
    12-word coin seed is 128 flips, not a handful."""
    s = ent.RollSession(source, words)
    n = s.target_rolls if rolls is None else rolls
    for i in range(n):
        s.add_roll((i % source.sides) + 1)
    return s


def _render(screen):
    canvas = Recording()
    screen.draw(App(), canvas)
    return canvas.drawn


def _coin_screens():
    """Every screen a coin ceremony can put on the glass, before the words."""
    s = _session(ent.COIN)
    full = _session(ent.COIN, rolls=None)
    entropy = full.final_entropy()
    concern = ent.assess_rolls(ent.COIN, full.rolls)
    yield "RollEntry", FG.RollEntryScreen(s)
    yield "EntropyCaptured", FG.EntropyCapturedScreen(
        full, entropy, mn.entropy_to_mnemonic(entropy))
    if concern is not None:
        yield "EntropyWarning", FG.EntropyWarningScreen(full, entropy, concern)


def test_no_coin_screen_says_roll():
    """The whole point, stated once. A coin user should never read dice words."""
    offenders = []
    for name, screen in _coin_screens():
        for line in _render(screen):
            if "roll" in line.lower():
                offenders.append("%s: %r" % (name, line))
    assert not offenders, "coin screens using dice wording:\n  " + "\n  ".join(offenders)


def test_the_coin_header_uses_its_own_verb():
    drawn = _render(FG.RollEntryScreen(_session(ent.COIN)))
    assert any("FLIPPING" in d.upper() for d in drawn), drawn[:6]


def test_the_coin_prompt_comes_from_the_source():
    """Not a second copy of the string living in the screen."""
    drawn = _render(FG.RollEntryScreen(_session(ent.COIN)))
    assert ent.COIN.prompt in drawn


def test_dice_wording_is_unchanged():
    """The coin fix must not quietly reword the dice path, which is what
    every user of this device sees first."""
    drawn = _render(FG.RollEntryScreen(_session(ent.D6)))
    assert ent.D6.prompt in drawn
    assert any("ROLLING D6" in d.upper() for d in drawn)
    assert not any("flip" in d.lower() for d in drawn)


def test_every_source_has_a_gerund_that_is_not_derived_wrongly():
    """Flip doubles its consonant and Roll does not. A source added later
    without a gerund would silently inherit 'Rolling'."""
    assert ent.COIN.gerund == "Flipping"
    for src in (ent.D6, ent.D8, ent.D12):
        assert src.gerund == "Rolling"
    for src in ent.SOURCES.values():
        assert src.gerund.endswith("ing"), src.name


def test_the_pattern_caveat_is_in_the_users_verb():
    full = _session(ent.COIN, rolls=None)
    concern = ent.assess_rolls(ent.COIN, full.rolls)
    if concern is None:
        return  # nothing to warn about with this input; covered above anyway
    drawn = " ".join(_render(
        FG.EntropyWarningScreen(full, full.final_entropy(), concern)))
    assert "dice" not in drawn.lower()
