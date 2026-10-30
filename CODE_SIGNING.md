# Code signing policy

Status: **applying to SignPath Foundation** (2026-10-30). Release artifacts are not
signed yet. This file is the public policy required by the application.

## Signed artifacts

Windows release builds are produced by the GitHub Actions workflow in
[`.github/workflows/release.yml`](.github/workflows/release.yml) from the source
in this repository. After acceptance, the same workflow submits the unsigned
artifacts to SignPath for signing:

- `convcore.exe` and `convcore-cli.exe` (the application binaries)
- `convcore-<version>-setup.exe` (the installer)

No artifact is signed from a developer machine. Every signing request is
approved manually by the project approver before SignPath signs it.

Free code signing provided by [SignPath.io](https://about.signpath.io),
certificate by [SignPath Foundation](https://signpath.org).

## Roles

This is currently a solo-maintained project. One person holds all roles; the
roles are separated here so the responsibility is explicit:

- **Author / committer**: Know5 — writes source and build scripts.
- **Reviewer**: Know5 — reviews every change, including build scripts and CI
  configuration, before it reaches `main`.
- **Approver**: Know5 — approves each release signing request in SignPath.

Multi-factor authentication is required on both the GitHub and SignPath accounts
before the application is submitted.

## Build provenance

- Source: <https://github.com/Know5/everythingcanturn>
- Release workflow: <https://github.com/Know5/everythingcanturn/actions/workflows/release.yml>
- Builds are reproducible from the repository with the pinned PyInstaller
  version in `requirements.lock.txt`; the desktop UI is built from `ui/` with
  the committed `package-lock.json`.
- Release tags (`v*`) trigger the workflow; the installer and portable zip are
  attached to the GitHub Release.

## Privacy

The application performs file conversion locally. It does not include
telemetry, analytics, accounts, or background network calls.

Network access happens only when the user explicitly requests it:

- `convcore engines install ...` downloads FFmpeg/UnRAR from the pinned URLs in
  `convcore/engines/manifest.py`.
- Opening the LibreOffice download page or the project repository uses the
  system browser.

Files selected for conversion are not uploaded to any server. This program will
not transfer any information to other networked systems unless specifically
requested by the user or the person installing or operating it.

## Uninstallation

The Windows release installer will register a standard uninstaller and a Start
Menu entry. The uninstaller will remove the application; downloaded engine
caches under `%LOCALAPPDATA%\convcore\engines` will be kept by default because
they are large and reusable, with an option to remove them. The portable zip
build has no installer and is removed by deleting its folder.

## Third-party licensing

Project source is MIT (see [`LICENSE`](LICENSE)). The distributed application
includes third-party components; notable ones:

| Component | License |
|---|---|
| PyMuPDF (`pymupdf`) | AGPL-3.0 or Artifex commercial |
| Pillow | MIT-CMU |
| pdf2docx | MIT |
| OpenCV | Apache-2.0 |
| pyarrow | Apache-2.0 |
| psd-tools / rawpy / vtracer / resvg-py | MIT |
| py7zr | LGPL-2.1-or-later |
| pypdf / mistune / lxml | BSD-3-Clause |
| FFmpeg, LibreOffice, UnRAR | external engines, downloaded separately |

Because PyMuPDF is AGPL-3.0, binary distributions that include PDF features are
provided under AGPL-3.0 terms; the corresponding source is this repository.
For a closed-source or commercial distribution, replace PyMuPDF or obtain an
Artifex commercial license first.
