#!/usr/bin/env python3
"""
MapArr Performance Profiling Script
Measures response times and scaling characteristics across various stack sizes.
"""
import time
import json
import os
import sys
import tempfile
import shutil
import tracemalloc

import httpx

BASE_URL = "http://localhost:9494"
RESULTS = []


def generate_compose(num_services: int) -> str:
    """Generate a synthetic docker-compose.yml with N services."""
    lines = ["services:"]
    roles = ["sonarr", "radarr", "lidarr", "readarr", "prowlarr",
             "qbittorrent", "sabnzbd", "nzbget", "deluge", "transmission",
             "plex", "jellyfin", "emby", "tautulli", "overseerr"]
    for i in range(num_services):
        name = f"{roles[i % len(roles)]}{'' if i < len(roles) else i}"
        lines.append(f"  {name}:")
        lines.append(f"    image: lscr.io/linuxserver/{roles[i % len(roles)]}:latest")
        lines.append(f"    container_name: {name}")
        lines.append(f"    volumes:")
        lines.append(f"      - ./config/{name}:/config")
        if i % 3 == 0:
            lines.append(f"      - /mnt/data/media:/data/media")
        elif i % 3 == 1:
            lines.append(f"      - /mnt/data/downloads:/data/downloads")
        else:
            lines.append(f"      - /mnt/other:/data/other")
        lines.append(f"    ports:")
        lines.append(f"      - \"{8000 + i}:{8000 + i}\"")
        lines.append(f"    environment:")
        lines.append(f"      - PUID=1000")
        lines.append(f"      - PGID=1000")
        lines.append(f"    restart: unless-stopped")
    return "\n".join(lines)


def create_stacks(base_dir: str, num_stacks: int, services_per_stack: int):
    """Create N stack directories, each with a compose file."""
    for i in range(num_stacks):
        stack_dir = os.path.join(base_dir, f"stack-{i:03d}")
        os.makedirs(stack_dir, exist_ok=True)
        compose = generate_compose(services_per_stack)
        with open(os.path.join(stack_dir, "docker-compose.yml"), "w") as f:
            f.write(compose)


def measure_pipeline_scan(stacks_path: str, label: str) -> dict:
    """Measure pipeline scan response time and return metrics."""
    client = httpx.Client(base_url=BASE_URL, timeout=120.0)

    # Set stacks path
    resp = client.post("/api/change-stacks-path", json={"path": stacks_path})
    if resp.status_code != 200:
        return {"label": label, "error": f"change-stacks-path failed: {resp.status_code}"}

    # Measure pipeline scan
    tracemalloc.start()
    mem_before = tracemalloc.get_traced_memory()
    t0 = time.perf_counter()
    resp = client.post("/api/pipeline-scan")
    t1 = time.perf_counter()
    mem_after = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    elapsed_ms = (t1 - t0) * 1000
    mem_peak_mb = mem_after[1] / (1024 * 1024)

    data = resp.json() if resp.status_code == 200 else {}
    total_services = data.get("total_media_services", 0)
    total_stacks = data.get("total_stacks", 0)

    result = {
        "label": label,
        "response_ms": round(elapsed_ms, 1),
        "mem_peak_mb": round(mem_peak_mb, 2),
        "total_services": total_services,
        "total_stacks": total_stacks,
        "status": resp.status_code,
    }

    # Now measure a single-stack analysis
    if data.get("stacks"):
        first_stack = data["stacks"][0]
        stack_path = first_stack.get("path", "")
        compose_file = first_stack.get("compose_file", "docker-compose.yml")
        services = first_stack.get("services", [])

        t2 = time.perf_counter()
        aresp = client.post("/api/analyze", json={
            "path": stack_path,
            "compose_file": compose_file,
            "services": services[:5] if len(services) > 5 else services,
        })
        t3 = time.perf_counter()
        result["analyze_ms"] = round((t3 - t2) * 1000, 1)
        result["analyze_status"] = aresp.status_code

    client.close()
    return result


def measure_concurrent(stacks_path: str, num_requests: int = 5) -> dict:
    """Fire N concurrent requests and measure behaviour."""
    import asyncio

    async def fire():
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=120.0) as client:
            # Ensure path is set
            await client.post("/api/change-stacks-path", json={"path": stacks_path})

            tasks = []
            for _ in range(num_requests):
                tasks.append(client.post("/api/pipeline-scan"))

            t0 = time.perf_counter()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            t1 = time.perf_counter()

            statuses = []
            errors = 0
            for r in results:
                if isinstance(r, Exception):
                    errors += 1
                    statuses.append(f"ERR: {type(r).__name__}")
                else:
                    statuses.append(r.status_code)
                    if r.status_code >= 500:
                        errors += 1

            return {
                "concurrent_requests": num_requests,
                "total_time_ms": round((t1 - t0) * 1000, 1),
                "statuses": statuses,
                "errors": errors,
            }

    return asyncio.run(fire())


def main():
    print("MapArr Performance Profiler")
    print("=" * 60)

    scenarios = [
        (5, 1, "5 services, 1 stack"),
        (20, 1, "20 services, 1 stack"),
        (50, 1, "50 services, 1 stack"),
        (10, 5, "10 services, 5 stacks"),
        (10, 20, "10 services, 20 stacks"),
        (10, 50, "10 services, 50 stacks"),
        (10, 100, "10 services, 100 stacks"),
    ]

    tmpdir = tempfile.mkdtemp(prefix="maparr_perf_")
    print(f"Working directory: {tmpdir}\n")

    try:
        for svc_count, stack_count, label in scenarios:
            scenario_dir = os.path.join(tmpdir, label.replace(", ", "_").replace(" ", "_"))
            os.makedirs(scenario_dir, exist_ok=True)
            create_stacks(scenario_dir, stack_count, svc_count)

            print(f"Testing: {label}...")
            result = measure_pipeline_scan(scenario_dir, label)
            RESULTS.append(result)
            print(f"  Pipeline: {result.get('response_ms', 'N/A')}ms | "
                  f"Analyze: {result.get('analyze_ms', 'N/A')}ms | "
                  f"Mem peak: {result.get('mem_peak_mb', 'N/A')}MB | "
                  f"Services: {result.get('total_services', 'N/A')}")

        # Concurrent test with medium scenario
        print("\nConcurrent test (5 simultaneous requests, 10svc x 20stacks)...")
        medium_dir = os.path.join(tmpdir, "10_services_20_stacks")
        concurrent = measure_concurrent(medium_dir, 5)
        print(f"  Total: {concurrent['total_time_ms']}ms | "
              f"Errors: {concurrent['errors']} | "
              f"Statuses: {concurrent['statuses']}")

        # Write results
        write_report(RESULTS, concurrent)
        print(f"\nResults written to PERF_RESULTS.md")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def write_report(results, concurrent):
    lines = [
        "# MapArr Performance Profiling Results",
        f"",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M')}",
        f"**Version:** 1.5.0",
        f"**Platform:** Windows 11, Python 3.13",
        "",
        "## Pipeline Scan Response Times",
        "",
        "| Scenario | Pipeline (ms) | Analyze (ms) | Peak Mem (MB) | Services Found | Stacks |",
        "|----------|--------------|-------------|--------------|----------------|--------|",
    ]

    for r in results:
        lines.append(
            f"| {r['label']} | {r.get('response_ms', 'ERR')} | "
            f"{r.get('analyze_ms', 'N/A')} | {r.get('mem_peak_mb', 'N/A')} | "
            f"{r.get('total_services', '-')} | {r.get('total_stacks', '-')} |"
        )

    lines.extend([
        "",
        "## Scaling Analysis",
        "",
        "### Service count scaling (single stack)",
    ])

    single_stack = [r for r in results if "1 stack" in r["label"]]
    if len(single_stack) >= 2:
        first = single_stack[0]["response_ms"]
        last = single_stack[-1]["response_ms"]
        ratio = last / first if first > 0 else 0
        svc_ratio = 50 / 5  # 50 svc vs 5 svc
        lines.append(f"- 5→50 services: {first}ms → {last}ms (×{ratio:.1f})")
        if ratio < svc_ratio * 1.5:
            lines.append("- **Linear or better** — acceptable scaling")
        else:
            lines.append(f"- **Potentially super-linear** — ratio ×{ratio:.1f} vs expected ×{svc_ratio:.0f}")

    lines.extend([
        "",
        "### Stack count scaling (10 services each)",
    ])

    multi_stack = [r for r in results if "10 services" in r["label"]]
    if len(multi_stack) >= 2:
        first = multi_stack[0]["response_ms"]
        last = multi_stack[-1]["response_ms"]
        ratio = last / first if first > 0 else 0
        lines.append(f"- 5→100 stacks: {first}ms → {last}ms (×{ratio:.1f})")

    lines.extend([
        "",
        "## Memory Profile",
        "",
    ])
    max_mem = max((r.get("mem_peak_mb", 0) for r in results), default=0)
    lines.append(f"- Peak memory across all scenarios: {max_mem}MB")
    if max_mem < 50:
        lines.append("- **Well within limits** — no memory concerns")
    else:
        lines.append(f"- **Elevated** — investigate memory usage in largest scenario")

    lines.extend([
        "",
        "## Concurrent Requests (5 simultaneous)",
        "",
        f"- Total wall time: {concurrent['total_time_ms']}ms",
        f"- Errors: {concurrent['errors']}",
        f"- Status codes: {concurrent['statuses']}",
    ])
    if concurrent["errors"] == 0:
        lines.append("- **All requests completed successfully** — no race conditions observed")
    else:
        lines.append(f"- **{concurrent['errors']} errors** — investigate race conditions")

    # Thresholds check
    lines.extend([
        "",
        "## Threshold Check",
        "",
        "| Threshold | Scenario | Actual | Verdict |",
        "|-----------|----------|--------|---------|",
    ])
    for r in results:
        if "20 services" in r["label"]:
            v = "PASS" if r.get("response_ms", 9999) < 500 else "FAIL"
            lines.append(f"| <500ms @ 20 svc | {r['label']} | {r.get('response_ms')}ms | {v} |")
        if "50 services, 1" in r["label"]:
            v = "PASS" if r.get("response_ms", 9999) < 2000 else "FAIL"
            lines.append(f"| <2000ms @ 50 svc | {r['label']} | {r.get('response_ms')}ms | {v} |")
        if "100 stacks" in r["label"]:
            v = "PASS" if r.get("response_ms", 9999) < 5000 else "FAIL"
            lines.append(f"| <5000ms @ 100 stacks | {r['label']} | {r.get('response_ms')}ms | {v} |")

    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "PERF_RESULTS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
