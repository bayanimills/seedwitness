# Visual design system

SeedWitness uses a portrait, touch-first interface for the CYD's 240 x 320
display. The device and simulator render from the same bitmap assets and share
the layout and colour definitions in `device/seedwitness/ui/theme.py`.

## Typography

The interface uses four monospaced bitmap-font sizes:

| Role | Face | Cell | Use |
|---|---|---|---|
| XS | MicroPython frame-buffer font | 8 x 8 | Dense secondary text, digests, footnotes and page indicators |
| S | Spleen | 8 x 16 | Captions, hints, paths and small controls |
| M | Spleen | 12 x 24 | Headers, primary content and ordinary button labels |
| L | Spleen subset | 16 x 32 | Roll values, seed words, address indices and key previews |

The L face contains only ` +-0123456789a-z`. Controls must check glyph
coverage before choosing it. Button labels use the largest covered face that
fits, and related buttons are harmonised to the smallest face required by the
group.

The generated Spleen modules are the canonical font source. Device builds
pack their glyphs into `/fonts.bin` and use flash-backed readers with the same
module interface. The simulator reads the same packed glyph bytes; it does not
substitute a desktop font. Font sources and licences are recorded in
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

## Geometry

- Screen: 240 x 320 portrait.
- Outer margin: 8 px.
- Header band: 36 px, with an amber title and a grey separator.
- Bottom action row: 40 px controls beginning 48 px above the lower edge.
- Ordinary touch targets are at least 34 px high.
- The address-index stepper uses 60 px square controls.
- Text wrapping and address grouping derive from the selected font's fixed
  cell width; screen code must not depend on approximate text measurement.

## Colour roles

The interface is drawn on black. Controls are defined by outlines rather than
mid-tone fills so their labels retain maximum contrast.

| Token | Meaning |
|---|---|
| `FG` | Primary content and idle labels |
| `MUTED` | Captions, hints and other secondary text |
| `ACCENT` | Headers, held controls and the value currently being chosen |
| `BUTTON_BORDER` | Rules, outlines and progress tracks |
| `GOOD` | A positive comparison result |
| `WARN` | A mismatch, invalid input or destructive warning |
| `GROUP_ALT` | Neutral alternation for four-character address groups |
| `STATE_EDGE` | Persistent device state, such as an enrolled account |
| `ENTROPY_BAR` / `CHECKSUM_BAR` | Entropy and BIP39-checksum portions |

`GOOD` and `WARN` are verdict colours. Ordinary data must not use them in a
way that could imply a result has already been verified.

## Components

- **TapButton:** an outlined touch target with automatic font fitting. Its
  border changes while held; committed states may use a solid fill.
- **Header band:** an M-face uppercase title plus a full-width rule. A 64 x 30
  Back or Home control occupies the top-right corner where required.
- **Hold confirmation:** consequential actions use the shared timed-hold
  interaction and complete only after the required dwell. Lifting early does
  nothing.
- **Progress bars:** a grey track with an inset semantic-colour fill. The
  PBKDF2 bar uses a fixed-width percentage field to overwrite prior values.
- **Word list:** six rows per page, with a small right-aligned index and a
  prominent word.
- **Addresses:** four-character groups alternate between white and neutral
  blue, giving the eye a resumable comparison point without signalling a
  verdict.
- **Keyboard:** large alphabet keys, smaller candidate words, a visible
  prefix cursor and a held-key preview.

## Rendering constraints

The hardware canvas converts RGB tuples to RGB565 and writes deterministic
bitmap cells to the ILI9341. Each Spleen face reuses one scratch buffer rather
than allocating per glyph. The simulator implements the same canvas contract
with PIL.

Device builds keep font data on flash because loading the generated Python
byte strings onto the MicroPython heap consumes substantially more memory
than their raw payload size. `tools/build_mpy.sh` verifies that every glyph
round-trips through the flash-backed reader before producing the deploy tree.

Visible changes must pass the clipping, glyph-coverage and deterministic
render tests. Hardware-dependent changes also require comparison against the
physical panel; simulator agreement alone does not establish touch alignment,
colour order or display-controller behaviour.
