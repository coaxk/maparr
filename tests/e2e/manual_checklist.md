# MapArr Manual Sign-Off Checklist

> Complete this checklist before tagging a release. These items require human judgement
> and cannot be automated. Check each item after visual inspection.

## Visual Quality

- [ ] Terminal boot animation plays smoothly (no flicker, no frozen frames)
- [ ] Service icons render at correct size (20px in rows, not blurry or oversized)
- [ ] YAML syntax highlighting is readable (changed lines stand out from unchanged)
- [ ] Health banner color is correct (green = ok, red/orange = problems)
- [ ] Health dot colors match service state in dashboard

## Layout & Responsiveness

- [ ] Directory browser modal renders correctly at 1280x720
- [ ] Directory browser modal renders correctly at 1920x1080
- [ ] Long stack names (30+ chars) don't break layout
- [ ] Many services (15+) render without overflow issues
- [ ] Paste area expands/collapses without layout jump

## User Experience

- [ ] Error messages are helpful (not raw Python tracebacks)
- [ ] Apply Fix confirmation modal clearly communicates "this writes to your files"
- [ ] Redeploy progress feels responsive (not frozen during docker compose up)
- [ ] Rate limit responses show a user-friendly message (not raw 429)
- [ ] Back button always returns to dashboard (never gets stuck on analysis screen)

## Cross-Browser (if applicable)

- [ ] Chrome: all journeys complete without errors
- [ ] Firefox: all journeys complete without errors (if supporting)
- [ ] Edge: all journeys complete without errors (if supporting)

## Docker Deployment

- [ ] Container starts from `docker compose up -d` with example compose
- [ ] Web UI loads on configured port
- [ ] Stacks are discoverable when mounted at /stacks
- [ ] Apply Fix works when /stacks is mounted read-write

---

**Sign-off:** ________________  **Date:** ________________
