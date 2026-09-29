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

### Changed
- Restored `_tmp` parameter passing to fix bridge and plugin temporary directory access
- Fixed user-specified frame rate being ignored in animated image to video conversion

## [0.1.0] - 2026-09-13

### Added
- Core conversion engine with hub-and-spoke architecture
- Format graph with Dijkstra path finding
- 9 domain support: raster, vector, media, document, markup, table, tree, archive, font
- 54 format support across all domains
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
- Comprehensive test suite (278 tests, 71% coverage)
- Security hardening: path traversal protection, zip bomb detection, command injection prevention
- Progress tracking and cancellation support
- Policy system for quality presets
- Thread-safe converter implementation
- Parquet format support in data domain
- Dynamic loss tier refinement based on file content
- Large file memory protection
- Automatic fallback when external engines fail

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
