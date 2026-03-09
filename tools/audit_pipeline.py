"""
Pipeline Audit Script — Runtime verification of every conflict type.

Runs each audit-stack through the actual analysis pipeline and captures:
- Conflicts detected (type, severity, services, description)
- Solution YAML generated (or None)
- Fix text content
- RPM mappings generated
- Health status
- Mount classifications

Outputs a detailed report identifying every disconnect.
"""

import json
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.analyzer import analyze_stack
from backend.mounts import classify_path
from backend.pipeline import run_pipeline_scan

AUDIT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit-stacks")

# ─── Mount Classification Tests ───

def test_classify_path():
    """Test classify_path() against known patterns."""
    print("\n" + "=" * 70)
    print("MOUNT CLASSIFICATION TESTS")
    print("=" * 70)

    test_cases = [
        # (path, expected_type, description)
        ("/mnt/c/Users/media", "wsl2", "Windows C: drive via WSL2"),
        ("/mnt/d/Downloads", "wsl2", "Windows D: drive via WSL2"),
        ("/mnt/nas", "local", "/mnt/nas — NAS mount, NOT WSL2"),  # BUG CHECK
        ("/mnt/nas/media", "local", "/mnt/nas/media — NAS subdir"),  # BUG CHECK
        ("/mnt/n/export", "local", "/mnt/n — single letter but not Windows"),  # BUG CHECK
        ("/mnt/user/data", "local", "/mnt/user — multi-letter mount"),
        ("/data", "local", "Standard Linux data path"),
        ("/opt/media", "local", "Standard Linux opt path"),
        ("nasserver:/export/media", "nfs", "NFS colon syntax"),
        ("nfs://server/path", "nfs", "NFS URL syntax"),
        ("//server/share/media", "cifs", "SMB UNC path (forward slash)"),
        ("\\\\server\\share\\media", "cifs", "SMB UNC path (backslash)"),
        ("C:\\Media", "windows", "Windows drive letter"),
        ("D:/Downloads", "windows", "Windows drive forward slash"),
        ("media_data", "named_volume", "Named Docker volume"),
        ("./config", "relative", "Relative config path"),
        ("../shared", "relative", "Relative parent path"),
        ("/mnt/a/something", "local", "/mnt/a — could be any mount"),  # BUG CHECK
        ("/mnt/z/data", "wsl2", "/mnt/z — rare but valid Windows drive"),
    ]

    results = []
    for path, expected, desc in test_cases:
        mc = classify_path(path)
        actual = mc.mount_type
        passed = actual == expected
        status = "PASS" if passed else "FAIL"
        results.append((status, path, expected, actual, desc))
        icon = "✓" if passed else "✗"
        print(f"  {icon} [{status}] {desc}")
        print(f"    Path: {path}")
        print(f"    Expected: {expected}, Got: {actual}")
        if not passed:
            print(f"    >>> MISMATCH — {desc}")
        print()

    pass_count = sum(1 for r in results if r[0] == "PASS")
    fail_count = sum(1 for r in results if r[0] == "FAIL")
    print(f"  Classification: {pass_count} PASS, {fail_count} FAIL")
    return results


# ─── Stack Analysis Tests ───

def analyze_audit_stack(stack_name, pipeline_context=None):
    """Run full analysis on an audit stack and return structured results."""
    stack_dir = os.path.join(AUDIT_DIR, stack_name)
    compose_file = None
    for fname in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
        fpath = os.path.join(stack_dir, fname)
        if os.path.isfile(fpath):
            compose_file = fpath
            break

    if not compose_file:
        return {"error": f"No compose file found in {stack_dir}"}

    # Read and parse compose
    with open(compose_file, "r") as f:
        raw_content = f.read()

    import yaml
    try:
        data = yaml.safe_load(raw_content)
    except yaml.YAMLError as e:
        return {"error": f"YAML parse error: {e}"}

    if not data or "services" not in data:
        return {"error": "No services in compose file"}

    # Run analysis
    result = analyze_stack(
        resolved_compose=data,
        stack_path=stack_dir,
        compose_file=compose_file,
        resolution_method="direct",
        raw_compose_content=raw_content,
        scan_dir=AUDIT_DIR,
        pipeline_context=pipeline_context,
    )

    # Extract key data
    result_dict = result.to_dict()

    return {
        "stack": stack_name,
        "status": result_dict.get("status", "unknown"),
        "conflicts": [
            {
                "type": c.get("type", ""),
                "severity": c.get("severity", ""),
                "description": c.get("description", "")[:120],
                "services": c.get("services", []),
                "fix_preview": (c.get("fix", "") or "")[:200],
                "has_fix": bool(c.get("fix")),
                "rpm_hint": c.get("rpm_hint", ""),
            }
            for c in result_dict.get("conflicts", [])
        ],
        "has_solution_yaml": result_dict.get("solution_yaml") is not None,
        "solution_yaml_preview": (result_dict.get("solution_yaml") or "")[:300],
        "has_original_corrected": result_dict.get("original_corrected_yaml") is not None,
        "has_rpm_mappings": len(result_dict.get("rpm_mappings", [])) > 0,
        "rpm_possible": any(m.get("possible") for m in result_dict.get("rpm_mappings", [])),
        "fix_summary": (result_dict.get("fix_summary") or "")[:200],
        "solution_changed_lines": result_dict.get("solution_changed_lines", []),
        "mount_warnings": result_dict.get("mount_warnings", []),
    }


def run_all_stack_tests():
    """Run analysis on every audit stack."""
    print("\n" + "=" * 70)
    print("STACK ANALYSIS TESTS")
    print("=" * 70)

    # First run pipeline scan to get pipeline context
    print("\n  Running pipeline scan on audit-stacks directory...")
    pipeline = run_pipeline_scan(AUDIT_DIR)
    pipeline_dict = pipeline.to_dict()
    print(f"  Pipeline: {pipeline_dict.get('stacks_scanned', 0)} stacks, "
          f"{pipeline_dict.get('media_service_count', 0)} services, "
          f"{len(pipeline_dict.get('conflicts', []))} conflicts")

    # Build pipeline context for cross-stack tests
    pipeline_context = {
        "total_media": pipeline_dict.get("media_service_count", 0),
        "conflicts": pipeline_dict.get("conflicts", []),
        "media_services": pipeline_dict.get("media_services", []),
    }

    # Test each stack
    stacks = sorted([
        d for d in os.listdir(AUDIT_DIR)
        if os.path.isdir(os.path.join(AUDIT_DIR, d)) and not d.startswith("_")
    ])

    all_results = []
    for stack_name in stacks:
        print(f"\n  {'─' * 60}")
        print(f"  STACK: {stack_name}")
        print(f"  {'─' * 60}")

        result = analyze_audit_stack(stack_name, pipeline_context)
        all_results.append(result)

        if "error" in result:
            print(f"  ERROR: {result['error']}")
            continue

        print(f"  Status: {result['status']}")
        print(f"  Conflicts: {len(result['conflicts'])}")
        for c in result["conflicts"]:
            print(f"    [{c['severity'].upper():8}] {c['type']}")
            print(f"             Services: {c['services']}")
            print(f"             Desc: {c['description']}")
            if c["fix_preview"]:
                # Show first line of fix
                first_line = c["fix_preview"].split("\n")[0]
                print(f"             Fix: {first_line}")
            if c["rpm_hint"]:
                print(f"             RPM hint: {c['rpm_hint'][:80]}")

        print(f"  Solution YAML: {'YES' if result['has_solution_yaml'] else 'NO'}")
        print(f"  Original Corrected: {'YES' if result['has_original_corrected'] else 'NO'}")
        print(f"  RPM Mappings: {'YES' if result['has_rpm_mappings'] else 'NO'} "
              f"(possible: {'YES' if result['rpm_possible'] else 'NO'})")
        print(f"  Changed Lines: {result['solution_changed_lines']}")

        if result["has_solution_yaml"]:
            print(f"  YAML Preview:")
            for line in result["solution_yaml_preview"].split("\n")[:8]:
                print(f"    | {line}")

    return all_results, pipeline_dict


# ─── Disconnect Analysis ───

def analyze_disconnects(stack_results, pipeline_dict):
    """Identify every disconnect between detection, solution, and rendering."""
    print("\n" + "=" * 70)
    print("DISCONNECT ANALYSIS")
    print("=" * 70)

    issues = []

    for result in stack_results:
        if "error" in result:
            continue

        stack = result["stack"]
        conflicts = result["conflicts"]

        # Check: permission-only stacks showing path YAML
        perm_types = {"puid_pgid_mismatch", "missing_puid_pgid", "root_execution",
                      "umask_inconsistent", "umask_restrictive", "cross_stack_puid_mismatch"}
        path_types = {"no_shared_mount", "different_host_paths", "named_volume_data", "path_unreachable"}
        infra_types = {"wsl2_performance", "mixed_mount_types", "windows_path_in_compose", "remote_filesystem"}

        conflict_types = {c["type"] for c in conflicts}
        has_path = bool(conflict_types & path_types)
        has_perm = bool(conflict_types & perm_types)
        has_infra = bool(conflict_types & infra_types)

        # DISCONNECT 1: Permission-only stack with solution YAML
        if has_perm and not has_path and result["has_solution_yaml"]:
            issues.append({
                "stack": stack,
                "severity": "CRITICAL",
                "issue": "Permission-only stack generates solution YAML (volume restructure)",
                "detail": f"Conflicts: {[c['type'] for c in conflicts]}, but solution YAML is path-based",
            })

        # DISCONNECT 2: Infrastructure-only stack with solution YAML
        if has_infra and not has_path and result["has_solution_yaml"]:
            issues.append({
                "stack": stack,
                "severity": "HIGH",
                "issue": "Infrastructure-only stack generates solution YAML",
                "detail": f"Conflicts: {[c['type'] for c in conflicts]}, YAML can't fix infra issues",
            })

        # DISCONNECT 3: RPM wizard for non-path issues
        if result["rpm_possible"] and not has_path:
            issues.append({
                "stack": stack,
                "severity": "HIGH",
                "issue": "RPM wizard shown for non-path issue",
                "detail": f"Conflicts: {[c['type'] for c in conflicts]}, RPM irrelevant",
            })

        # DISCONNECT 4: Conflicts found but no fix text
        for c in conflicts:
            if not c["has_fix"]:
                issues.append({
                    "stack": stack,
                    "severity": "MEDIUM",
                    "issue": f"Conflict {c['type']} has no fix text",
                    "detail": f"Severity: {c['severity']}, services: {c['services']}",
                })

        # DISCONNECT 5: Solution YAML with no changed lines
        if result["has_solution_yaml"] and not result["solution_changed_lines"]:
            issues.append({
                "stack": stack,
                "severity": "MEDIUM",
                "issue": "Solution YAML generated but no changed lines marked",
                "detail": "User can't see what changed in the YAML",
            })

        # DISCONNECT 6: Mixed conflicts — does solution cover both?
        if has_path and has_perm:
            # Solution YAML should address paths, but does the fix text address permissions?
            perm_fix_exists = any(
                c["has_fix"] for c in conflicts if c["type"] in perm_types
            )
            if not perm_fix_exists:
                issues.append({
                    "stack": stack,
                    "severity": "HIGH",
                    "issue": "Mixed path+permission stack missing permission fix text",
                    "detail": "Path YAML generated but no permission guidance",
                })

    # DISCONNECT 7: Pipeline health vs drill-down health
    pipeline_conflict_stacks = set()
    for c in pipeline_dict.get("conflicts", []):
        for s in c.get("services", []):
            pipeline_conflict_stacks.add(s)

    for result in stack_results:
        if "error" in result:
            continue
        if result["conflicts"] and result["status"] == "conflicts_found":
            # Check if any of this stack's services are in pipeline conflicts
            stack_services = set()
            for c in result["conflicts"]:
                stack_services.update(c["services"])

            in_pipeline = bool(stack_services & pipeline_conflict_stacks)
            if not in_pipeline and result["conflicts"]:
                issues.append({
                    "stack": result["stack"],
                    "severity": "MEDIUM",
                    "issue": "Drill-down finds issues but pipeline dashboard is blind",
                    "detail": f"Types: {[c['type'] for c in result['conflicts']]}, "
                              f"not visible in pipeline scan",
                })

    # Print report
    if not issues:
        print("\n  ✓ No disconnects found!")
    else:
        print(f"\n  Found {len(issues)} disconnects:\n")
        for i, issue in enumerate(sorted(issues, key=lambda x: {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(x["severity"], 9)), 1):
            print(f"  {i}. [{issue['severity']}] {issue['stack']}")
            print(f"     {issue['issue']}")
            print(f"     {issue['detail']}")
            print()

    return issues


# ─── Main ───

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

if __name__ == "__main__":
    print("MapArr Pipeline Audit — Runtime Verification")
    print("Testing every conflict type through the live analysis pipeline")
    print(f"Audit stacks directory: {AUDIT_DIR}")

    # Phase 1: Mount classification
    classify_results = test_classify_path()

    # Phase 2: Stack analysis
    stack_results, pipeline_dict = run_all_stack_tests()

    # Phase 3: Disconnect analysis
    issues = analyze_disconnects(stack_results, pipeline_dict)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    classify_fails = sum(1 for r in classify_results if r[0] == "FAIL")
    print(f"  Classification: {len(classify_results) - classify_fails}/{len(classify_results)} pass")
    print(f"  Stacks tested: {len(stack_results)}")
    print(f"  Disconnects found: {len(issues)}")

    crit = sum(1 for i in issues if i["severity"] == "CRITICAL")
    high = sum(1 for i in issues if i["severity"] == "HIGH")
    med = sum(1 for i in issues if i["severity"] == "MEDIUM")
    print(f"    CRITICAL: {crit}")
    print(f"    HIGH: {high}")
    print(f"    MEDIUM: {med}")
