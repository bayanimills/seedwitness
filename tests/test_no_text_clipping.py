"""No screen may draw text wider than the screen.

Both canvases clamp over-long strings to what fits and silently discard the
rest. That is the right failure mode for a stray call, but it means a layout
bug does not raise, does not warn, and does not look broken -- it just quietly
renders fewer characters than it was given.

On this device that is a correctness bug, not a cosmetic one. The whole point
of the address screen is character-by-character comparison against another
wallet, so dropping characters displays a WRONG address, which is exactly the
failure the device exists to catch. It has already happened once:
AddressDisplayScreen.LINE_WIDTH was hardcoded to 18 (correct for the old
320-wide landscape layout) and survived the move to 240-wide portrait, where
only 14 characters fit at 2x. A 42-character bech32 address rendered as 34,
losing 4 per line, and looked entirely plausible on screen.

So this asserts the invariant directly, for every text() call every screen
makes: what was asked for is what fits.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

import pytest  # noqa: E402
from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import entropy as ent  # noqa: E402
from seedwitness.ui import fonts  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui import theme as th  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)
BECH32 = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
TAPROOT = "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"


def _cell_w(scale, font):
    return font.width if font is not None else th.CHAR_W * scale


class RecordingCanvas(MockCanvas):
    """Records every text() call as asked for, before any clamping."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.calls.append((x, y, s, scale, font))
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    def overflowing(self):
        bad = []
        for x, y, s, scale, font in self.calls:
            if x + len(s) * _cell_w(scale, font) > self.width:
                bad.append((x, y, s, scale, font))
        return bad


def _bip(n):
    return next(t for t in S.drv.PATH_TEMPLATES if t.bip == n)


def _rolling(source_key, rolls=0):
    """A roll session part-way through, so the live digest is on screen."""
    s = ent.RollSession(ent.SOURCES[source_key], 12)
    for i in range(rolls):
        s.add_roll((i % s.source.sides) + 1)
    return S.RollEntryScreen(s)


def _captured():
    """A completed session, so the full entropy digest is on screen. This
    screen was missing from the sweep and was in fact silently truncating
    its hex dump (32 chars asked, 29 fit)."""
    s = ent.RollSession(ent.SOURCES["D6"], 12)
    i = 0
    while not s.is_complete:
        s.add_roll((i % 6) + 1)
        i += 1
    e = s.final_entropy()
    return S.EntropyCapturedScreen(s, e, S.mn.entropy_to_mnemonic(e))


def _screens():
    """One instance of every screen that renders variable-length content."""
    return [
        ("home", S.HomeScreen()),
        # every die size: the grid geometry and label scale differ per source,
        # and this is the most-tapped screen in the app
        ("roll_d6_empty", _rolling("D6")),
        ("roll_d6_mid", _rolling("D6", 20)),
        ("roll_d8", _rolling("D8", 5)),
        ("roll_d12", _rolling("D12", 5)),
        ("roll_coin", _rolling("COIN", 5)),
        ("entropy_captured", _captured()),
        ("method_select", S.MethodSelectScreen()),
        ("word_count", S.WordCountScreen("dice")),
        ("word_list_p0", S.WordListScreen(MNEMONIC, 0)),
        ("word_list_p1", S.WordListScreen(MNEMONIC, 1)),
        ("checksum", S.ChecksumWordScreen(MNEMONIC)),
        ("generate_complete", S.GenerateCompleteScreen(MNEMONIC)),
        ("qr_export", S.QRExportScreen(MNEMONIC)),
        ("verify_entry", S.VerifyEntryScreen()),
        ("demo_confirm", S.DemoConfirmScreen(lambda app: None)),
        ("manual_word_count", S.ManualWordCountScreen()),
        ("word_entry", S.WordEntryScreen(12)),
        ("invalid_seed", S.InvalidSeedScreen(12)),
        ("path_select", S.DerivationPathScreen(MNEMONIC)),
        ("address_index", S.AddressIndexScreen(MNEMONIC, _bip(84))),
        ("address_index_all", S.AddressIndexScreen(MNEMONIC, None)),
        ("all_addresses", S.AllAddressesScreen(MNEMONIC, 0, [
            (_bip(44), "m/44h/0h/0h/0/0", "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"),
            (_bip(49), "m/49h/0h/0h/0/0", "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf"),
            (_bip(84), "m/84h/0h/0h/0/0", BECH32),
            (_bip(86), "m/86h/0h/0h/0/0", TAPROOT),
        ])),
        ("addr_from_all", S.AddressDisplayScreen(
            MNEMONIC, _bip(86), 0, "m/86h/0h/0h/0/0", TAPROOT, from_all=True)),
        ("deriving", S.DerivingScreen()),
        ("diag", S.DiagScreen()),
        ("about", S.AboutScreen()),
        ("addr_bech32", S.AddressDisplayScreen(
            MNEMONIC, _bip(84), 0, "m/84h/0h/0h/0/0", BECH32)),
        ("addr_taproot", S.AddressDisplayScreen(
            MNEMONIC, _bip(86), 0, "m/86h/0h/0h/0/0", TAPROOT)),
        ("account_key_zpub", S.AccountKeyScreen(
            "xpub-test-only", _bip(84), "m/84h/0h/0h")),
        ("account_key_taproot", S.AccountKeyScreen(
            "xpub-test-only", _bip(86), "m/86h/0h/0h")),
        ("addr_qr_bech32", S.AddressQRScreen(BECH32, "m/84h/0h/0h/0/0")),
        ("addr_qr_taproot", S.AddressQRScreen(TAPROOT, "m/86h/0h/0h/0/0")),
        ("nickname", S.NicknameScreen("abcdefghij0123", lambda a, t: None)),
    ]


@pytest.mark.parametrize("name,screen", _screens(), ids=lambda v: v if isinstance(v, str) else "")
def test_screen_draws_no_text_past_the_right_edge(name, screen):
    canvas = RecordingCanvas()
    screen.draw(App(), canvas)
    bad = canvas.overflowing()
    assert not bad, "%s draws text past the right edge (it would be silently truncated): %r" % (
        name, bad)


def test_word_entry_candidate_list_fits():
    """The keyboard's suggestion row is built from live wordlist lookups, so
    its width depends on the longest matching word, not on a fixed string."""
    screen = S.WordEntryScreen(12)
    for prefix in ("a", "ab", "s", "sh", "t", "w", "z"):
        screen.prefix = prefix
        canvas = RecordingCanvas()
        screen.draw(App(), canvas)
        assert not canvas.overflowing(), "prefix %r overflows" % prefix


@pytest.mark.parametrize("address", [BECH32, TAPROOT])
def test_address_is_displayed_in_full(address):
    """The characters put on screen must reconstruct the address exactly --
    no dropped or duplicated run. The address renders as space-separated
    groups now, so this collects every ADDR_FONT call in draw order and
    checks the concatenation; group boundaries must never eat characters."""
    screen = S.AddressDisplayScreen(MNEMONIC, _bip(84), 0, "m/84h/0h/0h/0/0", address)
    canvas = RecordingCanvas()
    screen.draw(App(), canvas)
    # Only the address body: drawn in ADDR_FONT below the caption band, which
    # excludes the header/path/captions and any button label that harmonised
    # onto a different face.
    # Filter by POSITION, not by face. The screen steps the address down to a
    # smaller face when a long one would otherwise not fit above the buttons,
    # so pinning the face here made this test blind to exactly the case it
    # exists to catch: a 62-character taproot address used to render its sixth
    # line underneath the Enrol button, silently truncated, and this assertion
    # passed anyway because it only looked at the face it expected.
    button_top = min([b.y for b in screen.buttons] or [th.HEIGHT])
    chunks = [s for x, y, s, scale, font in canvas.calls
              if S.HEADER_H + 40 < y < button_top]
    joined = "".join(chunks)
    assert address in joined, (
        "address not rendered in full: got %r" % joined)


def test_address_line_width_is_derived_not_hardcoded():
    """A full line of groups must fit the usable width, so this cannot
    silently rot if the screen size or the face changes again."""
    cls = S.AddressDisplayScreen
    per_line = cls.GROUPS_PER_LINE * cls.GROUP + (cls.GROUPS_PER_LINE - 1)
    assert cls.GROUPS_PER_LINE >= 1
    assert per_line * cls.ADDR_FONT.width <= th.WIDTH - 2 * S.MARGIN


def test_button_text_scales_with_the_button():
    """The face is derived from the button, not set per screen, so the two
    cannot drift apart. A tall button with a short label takes a bigger face;
    the same label in a small button takes a smaller one."""
    big = S.TapButton(0, 0, 220, 90, "About", lambda a: None)
    small = S.TapButton(0, 0, 70, 24, "About", lambda a: None)
    assert big.char_h > small.char_h
    assert small.char_h >= th.CHAR_H


def test_auto_scaled_label_always_fits_its_button():
    """Whatever face is chosen, the text block must fit inside the button --
    otherwise the canvas would clamp it and silently drop characters."""
    cases = [
        (220, 90, "Generate\nNew Seed"), (220, 90, "Verify\nExisting Seed"),
        (220, 57, "BIP49 Nested (3...)"), (220, 57, "BIP86 Taproot (bc1p...)"),
        (108, 52, "6"), (69, 36, "12"), (34, 34, "w"), (70, 24, "Back"),
        (120, 32, "<- Back"), (220, 34, "Enter seed manually"),
    ]
    for w, h, label in cases:
        b = S.TapButton(0, 0, w, h, label, lambda a: None)
        lines = label.split("\n")
        need_w = max(len(x) for x in lines) * b.char_w
        need_h = len(lines) * b.char_h + (len(lines) - 1) * b.LINE_GAP
        assert need_w <= w, "%r at cell %d is %dpx wide in a %dpx button" % (
            label, b.char_w, need_w, w)
        assert need_h <= h, "%r at cell %d is %dpx tall in a %dpx button" % (
            label, b.char_h, need_h, h)


def test_hero_face_never_chosen_for_uncovered_labels():
    """The 16x32 face covers only digits/lowercase/signs. A label outside
    that set must skip it even when the geometry would allow it, or the
    device renders blank cells where letters should be."""
    covered = S.TapButton(0, 0, 220, 90, "12 words", lambda a: None)
    uncovered = S.TapButton(0, 0, 220, 90, "Dice: D6", lambda a: None)
    assert covered.font is fonts.L
    assert uncovered.font is not fonts.L
    assert uncovered.font is not None


def test_headers_hold_the_primary_face():
    """Titles are part of the design contract: every screen's header must
    render in the 12x24 face, not silently fall back to the caption face
    because a title grew past the space beside the Back corner. That
    fallback exists as a safety net; landing in it is a regression (the old
    AddressIndexScreen header clipped to 'BIP84 SEGWIT (BC1Q..')."""
    for name, screen in _screens():
        canvas = RecordingCanvas()
        screen.draw(App(), canvas)
        # header text: the first call inside the header band, accent colour
        header_calls = [c for c in canvas.calls
                        if c[1] < S.HEADER_H and c[4] is not None]
        if not header_calls:
            continue
        assert header_calls[0][4] is fonts.M, (
            "%s: header %r fell back to a smaller face" % (name, header_calls[0][2]))


def test_keyboard_keys_are_touchable():
    """Portrait halved the width; keys must not shrink below a usable target.
    ~24px is about 4mm on this 2.8in panel, near the practical floor for a
    fingertip on a resistive screen."""
    screen = S.WordEntryScreen(12)   # KEY_H is derived from the live layout
    assert screen.KEY_W >= 24
    assert screen.KEY_H >= 24


def test_every_button_label_fits_inside_its_own_button():
    """Fitting the SCREEN is not enough -- a label wider than its button spills
    over the neighbouring one and still looks like a valid choice. That is how
    the word-entry candidates broke: four columns on a 240px screen gave 50px
    buttons while the longest BIP39 word needs 64px at the smallest font."""
    for name, screen in _screens():
        for b in getattr(screen, "buttons", ()):
            lines = b.label.split("\n")
            need = max(len(line) for line in lines) * b.char_w
            assert need <= b.w, (
                "%s: label %r is %dpx wide at cell %d in a %dpx button"
                % (name, b.label, need, b.char_w, b.w))


def test_word_entry_candidates_fit_their_buttons():
    """Candidates come from live wordlist lookups, so their width depends on
    the longest matching word rather than on any fixed string."""
    screen = S.WordEntryScreen(12)
    for prefix in ("a", "ab", "s", "sh", "sha", "c", "d", "u", "z"):
        screen.prefix = prefix
        for b in screen._candidate_buttons():
            need = len(b.label) * b.char_w
            assert need <= b.w, (
                "prefix %r: candidate %r is %dpx in a %dpx button"
                % (prefix, b.label, need, b.w))


def test_longest_wordlist_word_fits_a_candidate_button():
    """Pin the worst case explicitly, so a future column-count change cannot
    quietly reintroduce the overflow for rare long words only."""
    from seedwitness.mnemonic import bip39
    words = list(bip39.WORDLIST)
    longest = max(words, key=len)
    screen = S.WordEntryScreen(12)
    w = (th.WIDTH - 2 * S.MARGIN
         - (screen.CAND_COLS - 1) * S.BUTTON_GAP) // screen.CAND_COLS
    assert len(longest) * th.CHAR_W <= w, (
        "longest word %r (%d chars) needs %dpx but candidate buttons are %dpx"
        % (longest, len(longest), len(longest) * th.CHAR_W, w))


def test_all_buttons_lie_within_the_screen():
    for name, screen in _screens():
        for b in getattr(screen, "buttons", ()):
            assert b.x >= 0 and b.y >= 0, "%s: button %r off the top/left" % (name, b.label)
            assert b.x + b.w <= th.WIDTH, "%s: button %r past the right edge" % (name, b.label)
            assert b.y + b.h <= th.HEIGHT, "%s: button %r past the bottom" % (name, b.label)


def test_about_pages_fit_vertically():
    """Help text is the easiest thing to over-write. Each page must fit above
    the nav row, or the last lines are simply not on the screen -- and unlike
    a clipped label, nothing about the layout looks wrong."""
    budget = (th.WIDTH - 2 * S.MARGIN) // th.CHAR_W
    # body must clear the nav row AND the "N of M" line drawn just above it
    limit = th.HEIGHT - S.AboutScreen.NAV_H - S.MARGIN - 16
    for i, (title, paras) in enumerate(S.AboutScreen.PAGES):
        y = S.AboutScreen.BODY_TOP
        for para in paras:
            if not para:
                y += 7
                continue
            for _ in th.wrap_text(para, budget):
                y += 13
        assert y <= limit, "About page %d (%s) runs to y=%d, limit %d" % (
            i + 1, title, y, limit)


def test_every_about_page_is_reachable_from_the_entry_page():
    """Only two nav buttons fit, so the links are chosen by hand -- which is
    exactly how a page ends up orphaned. Deriving them mechanically left the
    Trust page with nothing pointing at it."""
    n = len(S.AboutScreen.PAGES)
    seen = {0}
    frontier = [0]
    while frontier:
        page = frontier.pop()
        for target in S.AboutScreen(page)._nav_targets():
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    missing = [i for i in range(n) if i not in seen]
    assert not missing, "About pages unreachable from the entry page: %s" % (
        [S.AboutScreen.PAGES[i][0] for i in missing],)


def test_about_nav_targets_are_valid_and_not_self():
    n = len(S.AboutScreen.PAGES)
    for page in range(n):
        targets = S.AboutScreen(page)._nav_targets()
        assert targets, "page %d has no way out" % page
        for t in targets:
            assert 0 <= t < n, "page %d links out of range" % page
            assert t != page, "page %d links to itself" % page


def test_about_pins_the_two_primary_topics_on_every_page():
    """Generate and Verify are what the device does, so they are reachable in
    one press from anywhere in the help, always in the same place."""
    for page in range(len(S.AboutScreen.PAGES)):
        screen = S.AboutScreen(page)
        top = [b for b in screen.buttons if b.y < S.AboutScreen.BODY_TOP
               and b.label in S.AboutScreen.NAV_LABELS]
        labels = [b.label for b in top]
        expected = [S.AboutScreen.NAV_LABELS[i] for i in S.AboutScreen.TOP_LINKS]
        assert labels == expected, "page %d top row is %r" % (page, labels)


def test_about_prev_next_appear_only_where_they_lead_somewhere():
    n = len(S.AboutScreen.PAGES)
    for page in range(n):
        labels = [b.label for b in S.AboutScreen(page).buttons]
        assert ("< Prev" in labels) == (page > 0), "page %d prev wrong" % page
        assert ("Next >" in labels) == (page < n - 1), "page %d next wrong" % page


def test_about_leaves_no_page_stack_buildup():
    """Paging replaces rather than stacks, so Back exits About entirely
    instead of walking back through everything already read."""
    from seedwitness.ui.app import App
    app = App()
    app.push(S.AboutScreen(0))
    depth = len(app.stack)
    for _ in range(5):
        nav = next(b for b in app.screen.buttons
                   if b.label in S.AboutScreen.NAV_LABELS)
        nav.on_tap(app)
        assert len(app.stack) == depth, "About pages are accumulating on the stack"
