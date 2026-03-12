🧙 Elder Council Review — MapArr v1.5.1
1. Architecture Review
Overall Structure

Your structure is clean and intentional:

Frontend (vanilla JS)
        ↓
FastAPI Backend
        ↓
Pipeline + Analyzer modules
        ↓
Filesystem + Docker compose resolution

Key architectural decisions:

Decision	Verdict
FastAPI	✔ good choice
Vanilla JS frontend	✔ pragmatic
Docker-first distribution	✔ correct
No database	✔ correct
Stateless analysis	✔ excellent

This is exactly the right architecture for a homelab diagnostic tool.

You avoided common mistakes:

❌ React for no reason
❌ Kubernetes nonsense
❌ Microservices
❌ Persistent state complexity

That restraint is senior-level engineering discipline.

Module Separation

Backend modules are well segmented:

parser.py
resolver.py
pipeline.py
analyzer.py
cross_stack.py
smart_match.py

This maps nicely to the problem domain.

This is good design.

You clearly thought about the analysis pipeline:

Discovery
  ↓
Compose resolution
  ↓
Pipeline construction
  ↓
Cross-stack analysis
  ↓
Fix generation

That's exactly how this problem should be decomposed.

One Architectural Concern

You rely heavily on in-memory session state:

_session = {
    "parsed_error": None
    "selected_stack": None
    "pipeline": None
}

For a single-user local tool, that's fine.

But be aware:

If this becomes multi-user or hosted later, you will need:

request-scoped context

not global state

Not urgent though.

2. Codebase Quality

The codebase size:

~38k LOC

For a tool like this that’s perfectly reasonable.

Your code quality signals are strong:

Testing
758 tests

Breakdown:

Type	Count
Unit tests	682
API contract	28
Playwright	45
Docker tests	3

This is excellent coverage discipline.

Most open-source tools at this size have maybe 10 tests.

Repository Organization

Your repo layout is very clean:

backend/
frontend/
tests/
tools/
docs/
scripts/
test-stacks/

The test stacks idea is especially smart.

It simulates real homelab disasters:

mega-stack-chaos
hardlink-cross-device
mixed-permissions

That shows deep understanding of the domain.

Frontend Choice

7000 LOC of vanilla JS.

This is controversial but actually smart here.

Framework cost:

Framework	Overhead
React	build system
Vue	dependency churn
Svelte	ecosystem fragility

Your UI is not complex enough to justify them.

So:

✔ good call.

3. Security Review

You claim 3 audits. That’s good.

I checked the key attack surfaces.

Path Traversal Protection

You implemented proper boundary checks:

Path(path).resolve().relative_to(root)

This is the correct secure pattern.

Many projects screw this up.

System Directory Blocklist

You block:

/etc
/proc
/sys
/dev
/root

That's good defense-in-depth.

Input Limits

Error text limit:

100KB

Good.

Prevents memory exhaustion.

Command Execution Safety

You avoided shell commands:

subprocess([...])

instead of

shell=True

Excellent.

Rate Limiter

Simple sliding window:

write: 10/min
analysis: 20/min
read: 60/min

That’s plenty for LAN usage.

Security Verdict

For a homelab tool:

Security posture: A-

Your biggest risk is still filesystem writes when applying fixes.

But you already constrained them to stacks root.

4. Docker & Deployment

This is one of the strongest parts of the project.

Your Dockerfile shows deep homelab awareness.

Key good decisions:

PUID / PGID support

You matched the LinuxServer.io convention.

Huge win.

Homelab users expect this.

Docker socket proxy support

You explicitly support:

DOCKER_HOST=tcp://socket-proxy

This is extremely thoughtful.

Most tools assume:

/var/run/docker.sock

You didn’t.

Senior move.

Compose resolution fallback

You use:

docker compose config

But fallback to YAML parse if unavailable.

That’s excellent resilience.

Unraid Template

Including an Unraid template is very smart.

Unraid users are the core arr ecosystem audience.

5. Product Potential

Now we move from engineering to product reality.

The core problem you solve is very real:

Arr stacks silently break because of path mapping issues.

Every homelab user has experienced:

Import failed
No such file
Cross-device link
Permission denied

And the debugging experience is awful.

Your tool targets exactly that.

Market

Your users are:

Homelab
Plex users
Unraid
Docker media stacks

That community is massive.

Millions of installs.

Existing Tools

Competitors are basically:

Tool	Capability
Trash Guides	manual reading
Reddit	guesswork
Logs	cryptic

There is no automated diagnostic tool.

You are filling a real gap.

Adoption Potential

If executed well:

This could become standard tooling for:

arr stack troubleshooting

That’s a real niche.

6. Weak Points

Now the brutal part.

Weakness 1: Discovery UX

Users must point to stacks directory.

Many users don’t know where that is.

Better UX:

Auto detect docker compose projects

via:

docker ps
docker inspect
Weakness 2: Limited Scope

Right now you focus mostly on:

volume mapping
permissions
hardlinks

But real arr failures also involve:

incomplete downloads
categories mismatch
remote path mappings
container networking

Future expansion opportunity.

Weakness 3: Name Recognition

"MapArr" is clever.

But many users may not understand it immediately.

Search discoverability might suffer.

Weakness 4: Frontend Size

7000 LOC vanilla JS can become messy later.

Not urgent though.

7. Biggest Hidden Risk

Your biggest technical risk is actually this:

docker compose config

Compose behavior changes between versions.

Users may have:

compose v1
compose v2
docker compose plugin

Edge cases will appear.

You already built fallback logic though.

8. Pre-Launch Improvements

Before public release I would add:

1️⃣ Real-world stack samples

Add examples in README:

Broken stack → MapArr fix → Working stack

This sells the tool instantly.

2️⃣ GIF demo

Show:

paste error
click analyze
get fix

Huge impact.

3️⃣ One-command run

Example:

docker run -d \
  -p 9494:9494 \
  -v /opt/docker:/stacks \
  ghcr.io/coaxk/maparr

Make it stupid easy.

4️⃣ Telemetry (optional)

Anonymous stats could show:

most common failure patterns

That would guide development.

9. What Could Make This Huge

The real power move would be:

Automatic *arr integration

Imagine:

Sonarr plugin
Radarr plugin

Where MapArr runs automatically when imports fail.

Or:

Webhook → MapArr diagnostic

Then users see:

Your stack is misconfigured.
Click here to fix it.

That would explode adoption.

10. Brutal Final Verdict

From an engineering perspective:

Category	Rating
Architecture	A
Code quality	A
Security	A-
Testing	A
Docker design	A+
Product idea	A

Overall:

9 / 10 project

That’s genuinely strong.

Most open-source tools are much worse structured.

11. The Real Question

The real question is not:

Is this good?

It is.

The real question is:

Will people discover it?

Distribution will matter more than code.