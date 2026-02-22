# Changelog

## [1.5.0] - 2026-02-22

### Added
- **RPM Wizard** — 5-gate guided Remote Path Mapping setup as a Quick Fix alternative to mount restructuring
- **Solution track selector** — Choose between Quick Fix (RPM) and Proper Fix (restructure) for conflicts
- **Quick-switch combobox** — Click to browse all stacks or type to filter (all 3 search inputs)
- **Back to Stack List** navigation from analysis results
- **Pre-flight override source of truth** — Truthful reporting when user bypasses pre-flight on healthy stacks
- **Stale pipeline detection** — mtime-based auto-rescan before re-analysis after Apply Fix
- **Expanded download clients** — aria2, flood, rdtclient added to detection
- **Pipeline volume mount pairs** — `(source, target)` data for RPM calculation
- **Apply Fix pipeline refresh** — Full pipeline rescan after writing corrected YAML

### Fixed
- Pipeline majority root now captured regardless of within-stack conflicts
- Apply Fix expands affected services to all media services when pipeline override active
- Stale pipeline cache after Apply Fix no longer causes false re-analysis results

## [1.3.0] - 2026-02-20

### Added
- Test suite (360 tests) covering pipeline, analysis, cross-stack, smart-match, edge cases
- Auto-apply fix with `.bak` backup creation
- README documentation with Quick Start, architecture, troubleshooting
- CLAUDE.md for cross-Claude communication

## [1.2.0] - 2026-02-20

### Added
- Pipeline-first analysis — scan entire root directory, full media service awareness
- Cross-stack sibling detection for single-service stacks
- Smart error matching with confidence scoring

## [1.1.0] - 2026-02-20

### Added
- Mount intelligence — NFS, CIFS/SMB, WSL2, local mount detection with hardlink warnings
- Log panel with SSE streaming, drag-to-resize, level filtering
- Boot sequence with terminal animation and discovery
- SVG favicon
- Comprehensive backend logging

## [1.0.0] - 2026-02-20

### Added
- Initial release
- Fix Mode (paste error) and Browse Mode (browse stacks)
- Compose resolution via `docker compose config` with manual fallback
- Volume conflict detection (separate mount trees, inconsistent host paths)
- Solution YAML generation following TRaSH Guides pattern
- Diagnostic export (markdown)
- Dark theme UI
