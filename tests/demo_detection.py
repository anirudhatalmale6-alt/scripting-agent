"""
tests/demo_detection.py
───────────────────────
One-command demo that simulates both PR scenarios locally without hitting
the GitHub API.  Run with:

    python tests/demo_detection.py

What it does:
  1. Loads the two fixture JSON files.
  2. Calls detect_changes() directly (no network needed).
  3. Calls generate_all() for new features.
  4. Calls patch_all() for dependency upgrades.
  5. Prints a full Markdown report to stdout.
"""

import json
import sys
import os

# Make sure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.code_change_detector import detect_changes
from agent.script_generator import generate_all
from agent.script_patcher import patch_all
from agent.test_orchestrator import OrchestrationResult

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def run_scenario(label: str, fixture_file: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}\n")

    with open(fixture_file) as f:
        fixture = json.load(f)

    diff_text = fixture.get("diff_text", "")
    changed_files = fixture.get("changed_files", [])

    # ── Detection ─────────────────────────────────────────────────────────────
    print("[demo] Running change detection...")
    report = detect_changes(diff_text, changed_files)

    print(f"\nDependency changes : {len(report.dependency_changes)}")
    for d in report.dependency_changes:
        print(f"  • {d.package}: {d.old_version} → {d.new_version}")

    print(f"\nFeature changes    : {len(report.feature_changes)}")
    for f in report.feature_changes:
        print(f"  • {f.method} {f.path} — {f.description}")

    # ── Script generation (Scenario B only) ───────────────────────────────────
    generated = []
    if report.has_feature_changes:
        print("\n[demo] Generating test scripts for new features...")
        generated = generate_all(report.feature_changes, env="dev")
        for gs in generated:
            print(f"  k6         → {gs.k6_path}")
            print(f"  LoadRunner → {gs.loadrunner_path}")
            print(f"  Selenium   → {gs.selenium_path}")

    # ── Script patching (Scenario A + B) ──────────────────────────────────────
    print("\n[demo] Patching existing scripts...")
    patches = patch_all(report)
    patched = [p for p in patches if p.patched]
    print(f"  Patched {len(patched)}/{len(patches)} scripts")
    for p in patched:
        print(f"  • {p.file}: {p.reason}")

    # ── Markdown report ────────────────────────────────────────────────────────
    orch = OrchestrationResult(
        change_report=report,
        generated_scripts=generated,
        patch_results=patches,
        summary=f"Generated {len(generated)*3} scripts; patched {len(patched)} files.",
    )
    print("\n── Markdown PR Comment Preview ──────────────────────────────\n")
    print(orch.to_markdown())


if __name__ == "__main__":
    run_scenario(
        "SCENARIO A – Dependency Upgrade (requests 2.28 → 2.31)",
        os.path.join(FIXTURE_DIR, "sample_pr_scenario_a_dependency_upgrade.json"),
    )
    run_scenario(
        "SCENARIO B – New Feature (/api/wishlist endpoints)",
        os.path.join(FIXTURE_DIR, "sample_pr_scenario_b_new_feature.json"),
    )
    print("\n[demo] Done. Check scripts/dev/, scripts/loadrunner/, scripts/selenium/ for generated files.\n")
