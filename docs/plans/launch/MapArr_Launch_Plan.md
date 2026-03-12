# MapArr OSS Launch Plan
### Adapted from the AFFiNE/Gingiris OSS Launch Playbook
> Goal: Happy users solving real problems → organic community growth → awareness pipeline for ComposeArr, SubGen, and future tools

---

## Core Philosophy (MapArr Edition)

> The *arr community is tribal and will evangelize hard for tools that **genuinely solve their pain**. Stars are a byproduct. The real win is someone in r/selfhosted saying "this thing just fixed a problem I've been battling for months" — that post does more than 500 stars.

**This launch is not a spike. It's a foundation.**
Every community touchpoint you build for MapArr becomes the audience for ComposeArr, SubGen, and everything after it.

**On budget:** The Gingiris playbook budgets $11–22k on KOLs and paid channels. For MapArr, skip nearly all of that. The *arr/selfhosted community actively distrusts paid promotion and influencer-style posts. Zero budget, organic credibility — that's the play.

---

## Overall Timeline

| Phase | Timing | Core Tasks |
|-------|--------|------------|
| Strategic Prep | T-3 weeks | Positioning, README, demo assets |
| Channel Prep | T-2 weeks | Community presence, identify advocates |
| Pre-launch Polish | T-1 week | Docs tight, zero-friction install, demo GIF finalized |
| 🚀 **Launch Day** | T-0 | Multi-community posts, coordinated timing |
| Sustained Ops | T+1 to T+14 | Respond to everything, fix fast, compound reputation |

---

## Phase 1 — Strategic Prep (T-3 weeks)

### Nail the Positioning

MapArr isn't "a path mapping tool." It's **"the thing that finally explains why your Docker paths are broken and how to fix them."**

Every piece of copy should speak to the felt pain:
- "Why is Sonarr downloading to the wrong place?"
- "My volumes look right but nothing works"
- "I've been guessing at path mappings for hours"

**One-liner to use everywhere:**
> *MapArr reads your Docker Compose stack and shows you exactly where your path mappings break — and how to fix them.*

**Value proposition framing:**
MapArr has no direct competitor in the *arr space. The nearest thing is "go read the Trash Guides wiki and figure it out yourself." Lean into that: *"MapArr is the diagnostic tool the Trash Guides wiki couldn't be."*

### README Must-Haves
- **Problem statement first** — before any feature list, open with the pain (1-2 sentences)
- **Animated demo GIF** — showing the terminal boot sequence and a real fix being surfaced (non-negotiable)
- **Zero-friction install** — single `docker run` or `docker compose` snippet, above the fold
- **"Why MapArr"** section — short, punchy, no waffle
- **What it doesn't do** — honesty builds trust in technical communities
- **Links to ComposeArr and SubGen** — natural cross-mention, not a sales pitch
- Quick Start, FAQ, Contributor guide, License

### Asset Checklist
- [ ] Demo GIF/video — real broken stack → diagnosis → fix
- [ ] Screenshot of the most satisfying UI state
- [ ] Short reusable write-up of the problem
- [ ] Docker Hub / GHCR image published and tested
- [ ] Logo for dark and light backgrounds
- [ ] CHANGELOG.md exists

---

## Phase 2 — Community Presence (T-2 weeks)

### Where the Audience Lives

**Primary Subreddits:**

| Subreddit | Why | Approach |
|-----------|-----|----------|
| r/selfhosted | Core audience, Docker-heavy | Full launch post |
| r/homelab | Huge overlap, loves Docker tooling | Launch post, technical angle |
| r/sonarr | Direct path pain | Help first, launch second |
| r/radarr | Same | Same |
| r/PleX | Large, path issues endemic | "Fix your paths" angle |
| r/jellyfin | Growing fast, Docker-native crowd | Same |

*Note: The Gingiris channels list targets r/LocalLLaMA, r/MachineLearning, r/comfyui etc. — entirely wrong audience for MapArr.*

**Discord servers (high value):**
- Trash Guides Discord — the *arr community nerve center
- Official Sonarr / Radarr / Lidarr / Readarr discords
- Selfhosted.show Discord
- LinuxServer.io Discord

**Other:**
- LinuxServer.io forums
- Servarr wiki — a mention here is worth more than a Reddit post

### Pre-launch: Become Known

Before launch day, spend 1-2 weeks **actually helping people** in these communities. Answer path mapping questions. Reference MapArr casually only when directly relevant. This replaces the Gingiris "KOL outreach" phase entirely. In the *arr community, the most trusted voices are regular users who've been around.

---

## Phase 3 — Pre-launch Polish (T-1 week)

### Zero-Friction Checklist
- [ ] Cold install tested from scratch on a fresh machine
- [ ] `docker compose up` works without config touching
- [ ] Error messages are human — not stack traces
- [ ] Docs cover the top 3 questions newcomers will ask
- [ ] GitHub Issues enabled with a bug report template
- [ ] License set (MIT recommended)
- [ ] Repo is clean — no test files, no TODO comments in public code

### Final Copy Prep
Write all post copy in advance — don't improvise on launch day:
- [ ] r/selfhosted launch post (title + body + canned responses for common questions)
- [ ] r/homelab variant
- [ ] r/sonarr, r/radarr, r/jellyfin variants (shorter, more targeted)
- [ ] Discord announcement copy for each server
- [ ] GitHub release notes

---

## Phase 4 — Launch Day (T-0)

### Timing
Gingiris targets 9AM EST for US market. For *arr/selfhosted, evenings and weekends perform better — these are hobbyists. Aim for **Saturday or Sunday, 6–8PM EST**.

### Execution Schedule

| Time | Channel | Action |
|------|---------|--------|
| Day before | GitHub | Set repo public, verify all links |
| Morning of | Docker Hub / GHCR | Confirm image is live and pullable |
| 6:00 PM EST | r/selfhosted | Primary launch post |
| 6:30 PM | r/homelab | Launch post (variant) |
| 7:00 PM | Trash Guides Discord | Announcement |
| 7:00 PM | Other *arr Discords | Short announcement + link |
| 8:00 PM | r/sonarr, r/radarr | Targeted posts |
| 8:00 PM | r/jellyfin, r/PleX | Targeted posts |
| All evening | All channels | Monitor and respond to every comment |

### Post Title Formula
Lead with the problem, not the product name.

✅ Good:
- *"Built a tool that diagnoses path mapping issues in your *arr Docker stack — MapArr [free/OSS]"*
- *"Tired of guessing why your Docker volumes are broken? Made a diagnostic tool for *arr stacks"*
- *"Finally made the path mapping debugger I always wished existed for Sonarr/Radarr/etc"*

❌ Avoid:
- *"MapArr v1.0 — released!"*
- *"Check out my new open source project"*

### Launch Day Checklist
- [ ] Repo is public
- [ ] README looks good on GitHub (check mobile)
- [ ] Docker image pulls cleanly
- [ ] All links in README work
- [ ] r/selfhosted post is up
- [ ] r/homelab post is up
- [ ] Discord announcements sent
- [ ] *arr subreddit posts are up
- [ ] Responding to comments within the hour

### Emergency Handling

| Situation | Response |
|-----------|----------|
| Bug reported immediately | Fix fast, thank the reporter publicly, update in the thread |
| "This doesn't work for my setup" | Help them directly — it's free marketing |
| Negative reaction / skepticism | Don't delete, respond honestly. Technical communities respect candor. |
| Low traction first 2 hours | Don't panic. Evening weekend posts can run for 12–24 hours. |

---

## Phase 5 — Sustained Operations (T+1 to T+14)

### Daily Rhythm

The Gingiris playbook targets 100 stars/day and 20-30 KOL quotes/day. MapArr's equivalent: **2-3 genuine "this fixed my problem" comments in the first week.** That's the signal that matters.

| Activity | Frequency | Notes |
|----------|-----------|-------|
| Monitor GitHub Issues | 2× daily | Respond fast — first impressions matter |
| Monitor subreddit comments | 2× daily | Use Reddit notifications |
| Monitor Discord mentions | Daily | Be present in these servers |
| Post helpful content in communities | 2-3×/week | Genuinely helpful, contextual MapArr mentions |
| Release patch/fix if bugs found | As needed | Fast iteration = huge community trust |

### Content Redistribution
- [ ] Write a short Dev.to article about the path mapping problem (T+3 to T+7)
- [ ] Comment on related Docker/selfhosted articles with helpful context + MapArr link
- [ ] Consider a Medium post on the technical approach

*Skip: YouTube ads, Phantombuster automation, mass group distribution. These are general-audience tactics that will backfire in the *arr community.*

### Contributor Management
1. Tag easy issues with `good first issue` / `help wanted`
2. Review PRs quickly — a stale PR is demoralizing
3. Credit contributors visibly in release notes
4. If someone's contributing well, DM them — relationships compound

### Cross-Promotion Timing
Once MapArr has traction (T+7 to T+14):
- Mention ComposeArr naturally in the MapArr README and vice versa
- If people ask for features MapArr doesn't have, point them toward ComposeArr
- The shared audience is the asset — protect it by never over-promoting

---

## Data Tracking

| Metric | Tool | Frequency | What to watch |
|--------|------|-----------|---------------|
| GitHub Stars | Star-history.com | Daily | Trend, not spike |
| GitHub Traffic | GitHub Insights | Daily | Referral sources |
| Issues opened | GitHub | 2× daily | Bug reports = users |
| Reddit post traction | Reddit | +2hr, +24hr | Upvotes + comment quality |
| Discord mentions | Discord | Daily | Are people sharing it organically? |

---

## Post Templates

### r/selfhosted Launch Post

**Title:**
> Built a tool that diagnoses path mapping issues in your *arr Docker Compose stack — MapArr (free/OSS)

**Body:**
> If you've ever spent hours staring at your Sonarr/Radarr volumes wondering why files end up in the wrong place (or nowhere at all), this was built for you.
>
> **MapArr** reads your Docker Compose file and:
> - Maps out all your volume paths visually
> - Identifies where path mappings break or conflict
> - Tells you what's wrong and how to fix it
>
> [Demo GIF here]
>
> **Quick start:**
> ```bash
> docker run --rm -v /path/to/compose:/data ghcr.io/yourname/maparr
> ```
>
> GitHub: [link]
>
> It's early, feedback very welcome. Especially interested in weird edge cases from real stacks.

---

### Discord Announcement

> 🔧 **MapArr** — just released as open source
>
> If path mapping in your Docker *arr stack has ever driven you crazy, this might help.
>
> It reads your compose file and tells you exactly where the paths break and what to do about it.
>
> GitHub: [link] | Quick start in the README
>
> Happy to help if you hit any issues.

---

### *arr Subreddit Variant (r/sonarr, r/radarr, r/jellyfin)

**Title:**
> Made a free tool for diagnosing Docker path mapping issues in your *arr setup

**Body:**
> Path mapping confusion is one of the most common pain points I see here, so I built a diagnostic tool for it.
>
> MapArr reads your Docker Compose file and shows you where your volume paths break — and how to fix them.
>
> [Demo GIF]
>
> GitHub: [link]
>
> Works with any *arr stack. Let me know if it breaks on your setup — more edge cases = better tool.

---

## Key Differences from Gingiris Playbook

| Gingiris (AFFiNE) | MapArr |
|-------------------|--------|
| $11–22k KOL budget | $0 — organic only |
| Twitter/X primary channel | Reddit + Discord primary |
| Mass group distribution (500+ groups) | Targeted *arr communities only |
| KOL quote flooding | Genuine community presence |
| Product Hunt / HN submission | Maybe a Show HN post, skip PH |
| 100 stars/day target | "People actually fixed their stack" target |
| V2EX (Chinese tech community) | Not relevant |
| YouTube ads | Not relevant |
| Phantombuster automation | Not relevant (would backfire) |

---

## Ordered Action Items

**Right now / This week:**
- [ ] Record the demo GIF against a real broken stack
- [ ] Write the README (problem-first, GIF above fold, single install command)
- [ ] Publish Docker Hub / GHCR image
- [ ] Begin genuine community presence in target subreddits and discords

**T-1 week:**
- [ ] Write all launch post copy in advance
- [ ] Cold-test the install on a fresh machine
- [ ] Pick launch weekend (Saturday or Sunday evening EST)
- [ ] Have emergency fixes ready — assume you'll need to patch something

**Launch day:**
- [ ] Execute the post sequence above
- [ ] Stay online all evening to respond

**T+1 to T+14:**
- [ ] Fix any bugs fast and announce fixes in the original threads
- [ ] Write the Dev.to article
- [ ] Begin natural ComposeArr cross-mentions
