# Changelog

All notable changes to this project are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- First migration engine, `skyferry` (formerly `gdrive2sync` during development). It migrates Google Drive folder by folder through a buffer disk to the target and verifies in two stages: stage 1 compares Drive with the buffer by MD5, stage 2 compares the buffer with the target, including a check against the stage-1 manifest.
- Finished folders are renamed in Drive with the prefix `[MIGRIERT] `. The folder ID is checked before and after the rename, and renames never merge into an existing folder.
- Notifications via ntfy and healthchecks.io. By default no folder or file names are sent (`notify.include_paths = false`).
- Safety net: the Drive remote allows only reads and the prefix rename. Deleting, syncing and moving are blocked. The buffer guard checks the mount and the marker file before anything is written or deleted.
- Pluggable `SyncTarget` protocol with `FolderTarget` as the first implementation. 48 tests, including an end-to-end run without network access.
- systemd user service with an alarm unit for crashes. `install.sh` installs without sudo.
- Server setup helpers: packages, Google Drive connection through an SSH tunnel, and buffer disk preparation that looks the disk up by serial number. Helper scripts for the Wine-based Sync.com experiments.
- Open-source release files: English README, setup guide (English, and German in `docs/de/`), `CONTRIBUTING.md`, `SECURITY.md`, MIT license, CI workflows for tests and secret scanning.
