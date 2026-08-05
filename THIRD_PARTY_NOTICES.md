# Third-party notices

SeedWitness is licensed under the MIT License, but that licence does not
replace the licences of material incorporated from other projects. The source,
licence, and local treatment of each such component are recorded here.

## embit

- **Material:** `device/embit/`
- **Source:** [diybitcoinhardware/embit](https://github.com/diybitcoinhardware/embit)
- **Licence:** MIT
- **Local treatment:** pruned to imported modules; retained upstream files are
  unmodified.

The pinned source commit, complete MIT notice, and pruning details are in
[`device/embit/NOTICE.md`](device/embit/NOTICE.md). Some retained reference
implementations also carry their original copyright notices in their source
files.

## MicroPython ILI9341 and XPT2046 drivers

- **Material:** `device/seedwitness/drivers/ili9341.py` and `xpt2046.py`
- **Source:** [rdagger/micropython-ili9341](https://github.com/rdagger/micropython-ili9341)
- **Copyright:** Copyright (c) 2020
- **Licence:** MIT
- **Local treatment:** the touch driver is unmodified; the display driver's
  `clear()` implementation reuses a line buffer. The file headers describe the
  local change.

## micropython-lib modules

- **Material:** `device/mp_shims/hmac.py` and `device/mp_shims/hashlib/`
- **Source:** [micropython/micropython-lib](https://github.com/micropython/micropython-lib)
- **Copyright:** Copyright (c) 2013, 2014 micropython-lib contributors;
  SHA modules Copyright (c) 2023 Jim Mussared
- **Licence:** MIT
- **Local treatment:** vendored modules are identified in their file headers.

## MicroPython frame-buffer font

- **Material:** `sim/font_petme128_8x8.py`
- **Source:**
  [`extmod/font_petme128_8x8.h`](https://github.com/micropython/micropython/blob/master/extmod/font_petme128_8x8.h)
- **Copyright:** Copyright (c) 2013, 2014 Damien P. George
- **Licence:** MIT
- **Local treatment:** generated into an equivalent Python byte table for the
  desktop simulator; the device uses the copy built into MicroPython.

## Spleen bitmap fonts

- **Material:** `device/seedwitness/ui/fonts/spleen_*.py`
- **Source:** [fcambus/spleen](https://github.com/fcambus/spleen), v2.2.0
- **Copyright:** Copyright (c) 2018-2026 Frederic Cambus
- **Licence:** BSD 2-Clause
- **Local treatment:** generated Python bitmap tables. Each generated file
  records the exact source asset and SHA-256 digest.

## EFF large wordlist

- **Material:** `device/data/eff_large_wordlist.txt`
- **Source:** [EFF's large wordlist for five dice](https://www.eff.org/files/2016/07/18/eff_large_wordlist.txt)
- **Copyright:** Joseph Bonneau and the Electronic Frontier Foundation
- **Licence:** [Creative Commons Attribution 3.0 United States](https://creativecommons.org/licenses/by/3.0/us/)
- **Local treatment:** the five-digit dice indices were removed, leaving the
  same 7,776 words in the same order. The build assigns indices directly from
  that order.

## MIT licence text

The following terms apply to the MIT-licensed third-party components listed
above, together with their respective copyright notices:

> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in
> all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## BSD 2-Clause licence text

The following terms apply to the Spleen bitmap fonts, together with the
copyright notice above:

> Redistribution and use in source and binary forms, with or without
> modification, are permitted provided that the following conditions are met:
>
> 1. Redistributions of source code must retain the above copyright notice,
>    this list of conditions and the following disclaimer.
> 2. Redistributions in binary form must reproduce the above copyright notice,
>    this list of conditions and the following disclaimer in the documentation
>    and/or other materials provided with the distribution.
>
> THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
> AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
> IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
> ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
> LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
> CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
> SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
> INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
> CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
> ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
> POSSIBILITY OF SUCH DAMAGE.
