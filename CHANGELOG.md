# Changelog

## [1.5.0] - 2026-03-09

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
- **Multi-error detection** — Paste multiple errors, MapArr splits them and lets you pick which to diagnose
- **Infrastructure conflict warnings** — Three-tier guidance for auto-fixable, needs-followup, and infrastructure-required conflicts
- **Explanatory UX** — Mode selector intro, pipeline context, solution tab descriptions, TRaSH migration steps
- **Inline analysis progress** — Terminal title shows "Step X/6" in real-time via SSE
- **Error examples for all 14 conflict types** — Full coverage in Fix Mode example pills
- **Accessibility** — aria-labels on all interactive elements, icon-only buttons, and dynamic UI

### Docker & Deployment
- **PUID/PGID support** — LinuxServer.io-style entrypoint via gosu for seamless permission handling
- **Configurable port** — `MAPARR_PORT` environment variable (default 9494)
- **Docker CLI in image** — `docker compose config` works inside the container for full resolution
- **Socket proxy support** — Set `DOCKER_HOST` for Tecnativa/LinuxServer proxy; falls back gracefully
- **Log rotation** — docker-compose.yml includes logging limits (10m/3 files)
- **Non-root container** — Runs as UID/GID matching PUID/PGID (default 1000:1000)
- **Unraid template** — Community Applications XML at `unraid/maparr.xml`
- **Platform documentation** — QUICK_START.md covers Linux, Unraid, Synology DSM 7+, macOS, Windows/WSL2, Portainer

### Security
- **Dependency audit** — python-multipart CVE-2024-47874 patched; FastAPI, uvicorn, PyYAML bumped to safe versions
- **Write operation boundary** — Apply Fix requires explicit stacks root (MAPARR_STACKS_PATH)
- **Allowlist path validation** — Change Path uses allowlist when stacks root is configured
- **Bounded SSE queue** — maxsize=100 prevents memory exhaustion from slow consumers
- **SSE exponential backoff** — Reconnection delay escalates 5s→60s to prevent hammering
- **CSP readiness** — All inline onclick handlers migrated to addEventListener

### Fixed
- Pipeline majority root now captured regardless of within-stack conflicts
- Apply Fix expands affected services to all media services when pipeline override active
- Stale pipeline cache after Apply Fix no longer causes false re-analysis results
- CRLF line endings in pasted errors now handled correctly (Windows clipboard)
- SSE connection properly closed on page unload (prevents server-side leak)
- Analysis double-submit prevented via in-flight guard
- Stale analysis requests cancelled on mode switch via AbortController
- Compose file writes enforce LF line endings on all platforms
- Docker compose config timeout increased to 30s for socket proxy environments

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
