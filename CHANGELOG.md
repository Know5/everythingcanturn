# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- Web UI (`convcore ui`) now rejects requests whose `Host` header is not
  `127.0.0.1:<port>` or `localhost:<port>`. Previously a DNS-rebinding page could
  read the token from `/` and call every API. See `SECURITY_REVIEW.md` (2026-09-29).

### Added
- `tests/test_webui.py`: integration tests against a real server for token, Origin and Host checks
- Content constraints in path search: bridges declare what their output lacks (`BRIDGE_LACKS`),
  edges declare what they need (`BRIDGE_REQUIRES`, `Capability.requires`), and
  `Converter.refine_targets` probes what a specific file lacks
- `tests/test_reachability.py`: converts every listed target of each sample source end to end

### Changed
- Images, video frames and cover art are no longer offered as Word targets. That path wrapped
  the picture in a .docx through pdf2docx, contradicting pdf2docx's own scanned-PDF preflight;
  now that image-only PDFs are detected as scanned, it would be rejected anyway

### Fixed
- Conversion lists no longer offer targets that cannot work. A sweep converting every listed
  target of 17 sample sources found 288 of 698 failing or producing empty files. Now 471 are
  listed and 461 work; the other 10 depend on content (HTML without a table → CSV) or are
  checker false positives. Examples of removed targets: PNG → CSV / JSON / Markdown,
  PNG → MP3, WAV → PNG, silent video → MP3, scanned PDF → Markdown, SVG → plain text / Word / CSV
- `refine_targets` now returns only the targets the file can reach; callers iterate its result.
  Code that indexes the result with every key of `targets_for(...)` must switch to iterating it
- PDFs built from images now report their images, so scanned-page detection works on them
  (PNG → HTML used to "succeed" with an empty file)
- Audio without cover art → image now fails with a clear message instead of an ffmpeg exit code
- Odd-sized frames no longer break libx264 / libx265 / wmv2. Documents and data → MP4 / MKV /
  MOV / FLV / MPEG-TS always failed before, because rendered pages are 1240×1755
- Transparent sources to WebM no longer fail (VP9 now gets `-pix_fmt yuv420p` instead of `gbrap`)
- Web UI: a rejected POST (403) no longer resets the connection before the client reads the
  response. The handler now reads the request body before its checks
- Examples now perform real conversions on generated samples; previously every conversion
  call was commented out and used APIs that do not exist (`conv.convert(src, dst)`, `conv.batch`)
- README rewritten where it disagreed with the code: Python API, CLI examples, architecture,
  plugin interface, bridge table, limitations and the extension guide
- Corrected three 0.1.0 entries below that described behaviour the code never had

## [0.1.0] - 2026-09-13

### Fixed
- Restored `_tmp` parameter passing to fix bridge and plugin temporary directory access
- Fixed user-specified frame rate being ignored in animated image to video conversion

### Added
- Core conversion engine with hub-and-spoke architecture
- Format graph with Dijkstra path finding
- 9 domain support: raster, vector, media, document, markup, table, tree, archive, font
- 53 formats with verified read and write, plus 10 read-only formats
- 14 codec plugins with lazy loading
- Cross-domain bridges:
  - media ↔ raster (video frame extraction and image sequence to video)
  - table → document (table rendering to PDF)
  - document → table (PDF table extraction)
  - document → markup (PDF text extraction with heading inference)
  - vector → markup (SVG embedding in HTML)
  - table ↔ tree (bidirectional table/JSON conversion)
  - tree → markup (JSON/YAML rendering as readable documents)
  - markup → document (DocTree layout to PDF)
  - raster → document (images to PDF pages)
- GUI desktop application with drag-and-drop interface
- CLI with batch conversion and preset support
- Python API for programmatic usage
- External engine integration: FFmpeg, LibreOffice, resvg
- Comprehensive test suite (273 passing tests, 71% coverage)
- Security hardening: path traversal protection, zip bomb detection, command injection prevention
- Progress tracking and cancellation support
- Policy system for quality presets
- Concurrent batch conversion: `JobQueue`, GUI and Web UI create one `Converter` per task
  (corrected: originally listed as "thread-safe converter"; `Converter` has no internal locking)
- Parquet format support in data domain
- Dynamic loss tier refinement based on file content
- Input size cap: non-media inputs over 1 GiB are rejected
  (corrected: originally "large file memory protection"; there is no streaming or memory-pressure detection)
- ~~Automatic fallback when external engines fail~~ (corrected: never implemented; engine failures raise an error)

### Documentation
- Comprehensive README with architecture overview
- Security review documentation
- Installation guide with optional extras
- API usage examples
- Format compatibility matrix

### Infrastructure
- PyInstaller packaging for Windows
- Optional dependency groups for modular installation
- Coverage reporting with pytest-cov
- Git repository with 15+ commits tracking development progress

[Unreleased]: https://github.com/Know5/everythingcanturn/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Know5/everythingcanturn/releases/tag/v0.1.0
