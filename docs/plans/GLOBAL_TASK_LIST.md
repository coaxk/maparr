# MapArr — Global Task List

> **Live tracker:** [GitHub Project Board](https://github.com/users/coaxk/projects/1) — this is the active kanban. This file is the strategic context doc.
>
> Sources: Elder Council (DeepSeek + ChatGPT + Gemini + Grok), ChatGPT extended brainstorm, manual testing
> Council verdict: **A- overall, READY TO SHIP** — unanimous across all 4 Elders
> Last updated: 2026-03-13

---
---

# STAGE 1 — BEFORE PRIVATE BETA

> Everything here must be done before inviting beta testers.
> Current state: 18 done, 4 remaining (T2 + D1-D4)

---

### Security Hardening

| # | Task | Effort | Source | Status |
|---|------|--------|--------|--------|
| S1 | SSE connection limit — per-IP cap (~5) on `/api/logs/stream` | Done | DeepSeek | DONE |
| S2 | Document `/home` blocklist — QUICK_START + TROUBLESHOOTING | Done | DeepSeek | DONE |
| S3 | DOCKER_HOST allowlist — only allow `unix://` and `tcp://127.*`/`tcp://localhost`, refuse + log warning for anything else. Prevents SSRF via malicious/misconfigured env. | ~15 lines | Grok HIGH | DONE (`e91f4ed`) |
| S4 | Trusted proxy IP handling — `request.client.host` behind reverse proxy returns proxy IP, rate limiter bypassed. X-Forwarded-For parsing with configurable trust list via `MAPARR_TRUSTED_PROXIES`. | ~20 lines | Grok HIGH | DONE (`b24dc00`) |
| S5 | Force write boundary without MAPARR_STACKS_PATH — bare-metal dev runs allow apply-fix to write anywhere. Disable write endpoints (403) when no explicit root is set. | ~10 lines | Grok MED | DONE (`19838e5`) |

### Code Fixes

| # | Task | Effort | Source | Status |
|---|------|--------|--------|--------|
| C1 | Undo/Revert button — `.bak` restoration in Apply Fix UI. Endpoint swaps backup back. | Medium | Gemini + Grok | DONE (`7cf8856` + `e7e9dd4`) |
| C2 | Specific error messages — type-specific: YAML parse (line number), missing file, permission denied, Docker unreachable, unknown service. | Medium | DeepSeek + Gemini + Grok | DONE (`f31a756` + `7957428`) |
| C3 | Warning dismiss for Cat B low/medium — per-warning localStorage "I know, don't warn me again". Still logs. | Small | Gemini + Grok | DONE (`e38ccfc`) |
| C4 | SSE generator hard timeout — 5-minute max connection duration. Client reconnects transparently. | ~5 lines | Grok LOW | DONE (`324ec9d`) |

### Code Hygiene

| # | Task | Effort | Source | Status |
|---|------|--------|--------|--------|
| H1 | Unused imports cleanup — `ruff check --select F401` sweep across backend. | 5 min | DeepSeek + Grok | DONE (`c8fcc54`) |
| H2 | Dead/duplicated code — deduped COMPOSE_FILENAMES, cleaned legacy imports. | 15 min | Grok | DONE (`c8fcc54`) |

### Manual Testing

| # | Task | Effort | Source | Status |
|---|------|--------|--------|--------|
| T1 | Full UI smoke test (42 scenarios) — Round 1 complete. 14 pass, 28 fail → 7 bugs found, all fixed. 6 test expectation errors corrected. | ~2 hrs | Internal | DONE (Round 1) |
| T2 | Docker deployment test — build image, run with PUID/PGID, verify healthcheck, socket detection, Apply Fix writes through container, entrypoint edge cases. | ~30 min | Internal | TODO |

### UX Features (Promoted from v1.6)

| # | Task | Effort | Source | Status |
|---|------|--------|--------|--------|
| U1 | First-run wizard — 3-step onboarding: choose folder, set PUID/PGID, run scan. | Medium | Grok | DONE (`14c18e9`) |
| U2 | Collapsible "Other Stacks" section — default collapsed if >10 chips. localStorage memory. | Low | DeepSeek + Grok | DONE (`0fad1ea`) |
| U3 | Docker stack restart — "Restart Stack" button after Apply Fix. Socket capability check. | Medium | Gemini | DONE (`6407fe6`) |
| U4 | Export diagnostic zip — compose files + analysis + redacted secrets. | Low | Grok | DONE (`c9430e7`) |
| U6 | Redeploy risk banner — persistent inline banner until restart confirmed. | Low | Grok | DONE (`0aad850`) |
| U7 | Service icon fallback — segment matching + image keyword matching before generic.svg. | Low | Grok | DONE (`c3c8289`) |

### Documentation & Release Prep

| # | Task | Effort | Source | Status |
|---|------|--------|--------|--------|
| D1 | Comprehensive docs review — audit README, QUICK_START, TROUBLESHOOTING, CHANGELOG against v1.5.1. Version numbers, endpoints, features, examples, env vars. | Medium | Internal | TODO |
| D2 | GitHub repo polish — description, topics/tags, social preview, issue/PR templates, contributing guidelines. README tells the MapArr story in 30 seconds. | Medium | Internal | TODO |
| D3 | GIF demo for README — 10-second animated GIF: paste error, analyze, diagnosis, fix. Above the fold. "Distribution matters more than code." | Small | ChatGPT | TODO |
| D4 | Private beta release plan — tag format, release notes template, Docker image tagging (beta vs latest), tester selection, feedback method. **Key idea: adapt the testing dashboard HTML into a beta tester feedback tool** — structured scenarios + pass/fail/notes instead of unstructured bug reports. Beta duration + success criteria for promoting to stable. Public announcement channels (Reddit, Unraid) for after private beta. | Medium | Internal | TODO |

---
---

# STAGE 2 — v1.5.2 FIRST PATCH

> Within 1 week of private beta launch.
> Quick fixes from beta feedback + anything deferred from Stage 1.

---

| # | Task | Notes |
|---|------|-------|
| P1 | Bug fixes from beta testing | TBD — shaped by tester JSON exports |
| P2 | Anything deferred from Stage 1 that didn't block launch | TBD |

---
---

# STAGE 3 — v1.6 CODE HEALTH + UX

> "Pay down tech debt + add the features the Elders asked for."

---

### Backend Restructuring

| # | Task | Effort | Source |
|---|------|--------|--------|
| B1 | Split `analyzer.py` (3,942 LOC) → `conflicts.py`, `permissions.py`, `solutions.py`. Thin orchestrator stays. All 755 tests pass without modification. | High | All 4 Elders |
| B2 | Split `main.py` (1,277 LOC) → `routes/` (endpoints), `security.py` (rate limiter, path validation, blocklist), `session.py` (state management). Cuts cognitive load ~70%. | High | Grok |
| B3 | Session state extraction with locking — extract `_session` into own module with proper guards. Prevents "session bleed" bugs. Foundation for multi-worker. | Medium | Grok + DeepSeek |
| B4 | Per-compose analysis cache — cache by mtime + content hash. Pipeline re-scan skips unchanged stacks. ~80% CPU savings at 100+ stacks. | Medium | Grok |
| B5 | Role strings → enum — replace hard-coded "arr", "download_client", "media_server" with Python enum. Type safety + IDE support. | Low | Grok |
| B6 | Rate limiter dict size cap — hard cap 10,000 IPs, evict oldest on overflow. One-liner. | Trivial | Gemini |

### Frontend Restructuring

| # | Task | Effort | Source |
|---|------|--------|--------|
| F1 | ES module split for `app.js` (~8.5K LOC) → `pipeline-dashboard.js`, `analysis-view.js`, `apply-fix.js`, `rpm-wizard.js`, `sse-client.js`, `state.js`, `utils.js`. No framework. Native ES modules or simple bundler. | High | All 4 Elders |
| F2 | Reduce boot icon preloads — 179 icons every boot. Lazy-load or limit to detected services only. | Low | Grok |

### UX Features (remaining in v1.6)

| # | Task | Effort | Source |
|---|------|--------|--------|
| U5 | One-click "apply all safe fixes" — cross-stack batch apply for all Cat A + B fixes. Extends existing `/api/apply-fixes` to pipeline scope. | Medium | Grok |
| U8 | Stack Health Score — numeric 0-100, A-F grades. Weighted: path (heavy), hardlinks (medium), permissions (medium), platform (light). Port from ComposeArr `scoring.py`. | Medium | ChatGPT |

### Visualization

| # | Task | Effort | Source |
|---|------|--------|--------|
| V1 | Pipeline visualization — interactive graph: Indexer → Prowlarr → Sonarr → qBit → /downloads → /media → Plex. Broken links in red. The "aha moment" interface. D3.js / dagre / CSS+SVG. | High | ChatGPT |
| V2 | Filesystem topology visualizer — host path → container path mount table. Cross-device boundaries + hardlink failure points highlighted. Visual "why hardlinks don't work." | Medium | ChatGPT |

### Platform

| # | Task | Effort | Source |
|---|------|--------|--------|
| PL1 | Unraid-native scan — detect `/mnt/user` vs `/mnt/cache` vs `/mnt/disk*` splits. Recommend unified `/mnt/user/data`. Validate `nobody:users` (99:100) PUID/PGID. Unraid is the largest *arr platform. | Medium | ChatGPT |

---
---

# STAGE 4 — v2.0 STRATEGIC

> "MapArr becomes proactive."

---

### Features

| # | Task | Effort | Source |
|---|------|--------|--------|
| SA1 | Import Failure Autopsy — poll Sonarr/Radarr `/api/v3/history` for failed imports, auto-trigger analysis. "Sonarr failed 2 minutes ago — root cause: cross-device hardlink." Reactive → proactive. Biggest adoption unlock. | High | ChatGPT |
| SA2 | Auto-detect stacks via `docker ps` / `docker inspect` — discover running containers via Docker API, reconstruct compose context. Users don't need to know where compose files live. Valuable for Unraid/Portainer. | High | ChatGPT |
| SA3 | Webhook integration with arr apps — register as webhook receiver in Sonarr/Radarr. Real-time import failure events without polling. Zero-config Import Failure Autopsy. | Medium | ChatGPT |

### Architecture Debt

| # | Task | Effort | Source |
|---|------|--------|--------|
| AD1 | Compose↔Analysis abstraction layer — new conflict types touch ~6 files. Extract formal interface. Self-contained conflict modules that register themselves. Foundation for plugin system. | High | Grok |
| AD2 | Deduplicate Docker CLI fallback — `force_manual=True` exists in both `resolver.py` and `pipeline.py`. Single source of truth in resolver. | Low | Grok |
| AD3 | RPM Wizard backend migration — calculation logic in frontend JS, solution YAML in backend Python. Move RPM logic to backend API, frontend becomes presentational. Prevents pain as fix tracks grow. | Medium | Grok |

---
---

# STAGE 5 — v3.0+ ECOSYSTEM VISION

> Long-term ideas. Not planned. Documented for future reference. (ChatGPT brainstorm)

---

| # | Task | Notes |
|---|------|-------|
| EV1 | StackSenseArr — continuous controller. Live pipeline model, drift detection, auto-healing. Separate service using MapArr's engine as a library. | Separate product |
| EV2 | Stack Simulator — pre-deployment prediction. Simulate pipeline before `docker compose up`, predict failures before they happen. | Extension of MapArr |
| EV3 | Failure pattern learning — anonymized diagnostics across installations. Common pattern ranking, "most likely cause" predictions. Opt-in telemetry with strong privacy. | Privacy-sensitive |
| EV4 | `composearr install media-stack` — Helm-style generation. One command → best-practices stack. ComposeArr generates, MapArr validates, SubBrainArr optimizes. | Cross-ecosystem |
| EV5 | ArrOps ecosystem branding — unified identity across MapArr + ComposeArr + SubBrainArr. "DevOps for media stacks." | Marketing decision |

---
---

# EXCLUDED

> Items considered and intentionally dropped, with reasoning.

---

| Item | Source | Why excluded |
|------|--------|-------------|
| CSRF protection | Gemini | Local-network, no session cookies. Near-zero real-world risk. |
| Multi-tab state safety | Gemini | Single-user local tool. Revisit if multi-user. |
| Telemetry | ChatGPT | Privacy-sensitive audience. v3.0+ discussion after trust. |
| Type hints retrofit | DeepSeek | High effort, low value vs test suite. Add to new code only. |
| Hardlink onboarding tooltip | DeepSeek+ChatGPT | Analysis engine already explains failures with fixes. TRaSH Guides linked. |
| Dark/light mode toggle | Gemini+Grok | Dark theme matches homelab aesthetic. Nobody's asked. Revisit on demand. |
| Expand Image DB | Gemini | 219 images + custom-images.json covers it. Add on demand. |
| Backup atomic swap | Grok | `shutil.copy2` then write not atomic. Rate limit + single-user makes race theoretical. |
| `uvicorn --workers >1` support | Grok | Ships single-worker. Document assumption. Session locking in v1.6 as foundation. |
