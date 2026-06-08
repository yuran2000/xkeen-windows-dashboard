# Third-Party Notices

This project bundles (vendors) a copy of the following third-party component.
It retains its original copyright and is used under its own license; the full
license text is kept next to its source.

## rkn-block-checker

- Vendored under: `rkn_checker/`
- Copyright (c) 2026 Dmitry Vinogradov
- License: MIT
- Source: https://github.com/MayersScott/rkn-block-checker
- Full license text: `rkn_checker/LICENSE`

When redistributing this project (including the `rkn_checker/` folder), the
above copyright notice and the `rkn_checker/LICENSE` file must be retained.

## Architectural inspiration

The protobuf parser for v2fly geosite/geoip `.dat` files in
`geosite_scanner.py` (added in v1.0.81) was independently re-implemented in
Python from scratch, but its low-level decoder structure (varint + length-
delimited reads + tag/wire-type walk) was modelled after the Rust
implementation in:

- **zxc-rv/XKeen-UI** — `backend/src/geo.rs`
- License: MIT
- Source: https://github.com/zxc-rv/XKeen-UI

No code was copied verbatim — both implementations independently follow the
same v2fly protobuf wire-format specification. Acknowledged here as
transparency and good-faith open-source practice (not legally required by
MIT, since architectural patterns / algorithms are not covered by copyright).
