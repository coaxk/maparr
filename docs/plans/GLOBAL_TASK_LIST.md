# MapArr Global Task List

> Sources: Elder Council Review (DeepSeek + ChatGPT + Gemini) + ChatGPT extended brainstorm
> Council verdict: **A- overall, READY TO SHIP** with 2 pre-release fixes

---

## v1.5.1 — Pre-Release (do before tagging)

- [ ] **SSE connection limit** — Add per-IP concurrent connection cap (~5) on `/api/logs/stream`. Only remaining security surface. (DeepSeek)
- [ ] **Document `/home` blocklist** — Add explanation in QUICK_START.md + TROUBLESHOOTING.md for why `/home` is in `_BLOCKED_PREFIXES` and where to store stacks instead. (DeepSeek)

## v1.5.2 — Ship Soon (within a week of launch)

- [ ] **Undo/Revert button** — Expose `.bak` file restoration in Apply Fix UI. Backend already creates backups. High trust impact. (Gemini)
- [ ] **Specific error messages** — Replace "Analysis failed — check log panel" with type-specific messages: YAML parse error vs missing file vs permission denied vs service unreachable. (DeepSeek + Gemini)
- [ ] **Warning dismiss for root execution** — "I know, don't warn me again" per-warning type for power users intentionally running as root. (Gemini)
- [ ] **Unused imports cleanup** — Quick sweep across backend modules. 5-minute win. (DeepSeek)
- [ ] **GIF demo for README** — Paste error → click analyze → get fix. Distribution matters more than code. (ChatGPT)

## v1.6 — Next Major

- [ ] **Split `analyzer.py`** (3,942 LOC) — Extract into `conflicts.py`, `permissions.py`, `solutions.py`. Unanimous recommendation. (All 3 Elders)
- [ ] **ES module split for `app.js`** (~7K LOC) — Split into logical modules. No framework needed. Unanimous. (All 3 Elders)
- [ ] **Collapsible "Other Stacks" section** — Reduces dashboard clutter at scale. (DeepSeek)
- [ ] **Direct stack restart via Docker socket** — Socket is already mounted. Closes the fix → restart loop. (Gemini)
- [ ] **Rate limiter dict size cap** — One-liner hardening: cap max tracked IPs. Not urgent but cheap. (Gemini)
- [ ] **Stack Health Score** — Numeric 0-100 score across the 4 existing analysis passes. ComposeArr already has this pattern (scoring.py). Present as headline number on dashboard. (ChatGPT brainstorm)
- [ ] **Pipeline visualization** — Visual graph of service → service → path flow. Internal graph model exists, surface it as a diagram. The "aha moment" interface. (ChatGPT brainstorm)
- [ ] **Filesystem topology visualizer** — Host paths → container paths mapped visually. Shows why hardlinks fail at a glance. Extension of pipeline viz. (ChatGPT brainstorm)
- [ ] **Unraid-native scan** — Detect `/mnt/user` vs `/mnt/cache` split, Unraid-specific recommendations. Unraid is #1 target audience. Template already exists. Extend Platform Pass 4. (ChatGPT brainstorm)

## v2.0 — Future Consideration

- [ ] **Import Failure Autopsy** — Poll Sonarr/Radarr `/api/v3/history` for failed imports, auto-trigger MapArr analysis. Lowest architectural change, highest user impact. ChatGPT's #1 strategic recommendation. (ChatGPT brainstorm)
- [ ] **Auto-detect stacks via `docker ps` / `docker inspect`** — Users wouldn't need to know their stacks directory. Changes core UX model. (ChatGPT)
- [ ] **Webhook integration with arr apps** — Auto-trigger MapArr diagnostic on Sonarr/Radarr import failure. "Your stack is misconfigured. Click here to fix it." (ChatGPT)

## v3.0+ — Aspirational / Ecosystem Vision

> These ideas from the ChatGPT extended brainstorm represent the long-term vision. Not planned, but documented for future reference.

- [ ] **StackSenseArr / continuous controller** — Live pipeline model with drift detection and self-healing. Fundamentally different product.
- [ ] **Stack Simulator** — Pre-run prediction: simulate pipeline before deploying. Requires ComposeArr integration.
- [ ] **Failure pattern learning** — Anonymized diagnostic data → intelligent recommendations. Needs telemetry infrastructure.
- [ ] **`composearr install media-stack`** — Helm-style stack generation with best practices baked in. Requires ComposeArr maturity.
- [ ] **ArrOps ecosystem branding** — Unified platform identity across MapArr + ComposeArr + SubBrainArr. Marketing decision for after community traction.

## Excluded (with rationale)

| Item | Source | Why excluded |
|------|--------|-------------|
| CSRF protection | Gemini | Local-network tool, no session cookies. Near-zero real-world risk. |
| Multi-tab state safety | Gemini | Single-user local tool. v2.0 concern if multi-user ever happens. |
| Telemetry | ChatGPT | Privacy-sensitive homelab audience. Not appropriate to plan now. |
| Type hints across codebase | DeepSeek | High effort, low immediate value. |
| Hardlink onboarding tooltip | DeepSeek+ChatGPT | Tool already links to TRaSH Guides. Low incremental value. |
| Dark/light mode toggle | Gemini | Already have dark theme. Marginal gain. |
| Expand Image DB | Gemini | 219 images covers the ecosystem. Add on demand. |
