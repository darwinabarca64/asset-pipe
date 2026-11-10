"""Architectural, OPSEC Hygiene, and Asymmetric LOC Guardian Suite.

Enforces Clean Architecture directory structure, zero local identity/path leaks
('[OPSEC]'), strict zero-emoji policy across all repository files, and the
asymmetric 250-LOC modularity ceiling.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SELF_PATH = Path(__file__).resolve()
MAX_STANDARD_LOC = 250

DOC_EXEMPT_NAMES: set[str] = {
    "dashboard_view.ts",
    "server.ts",
    "sql_workbench_server.py",
    "README.md",
    "Data-architecture.md",
    "MANUAL-MAESTRO-DARWIN-ES.md",
    "package-lock.json",
}

IGNORED_DIRS: set[str] = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "data",
    ".minio_data",
    ".antigravity",
}

IGNORED_OPSEC_FILES: set[str] = {
    "hardware_audit_report.json",
    ".wslconfig.recommended",
    "MANUAL-MAESTRO-DARWIN-ES.md",
    ".env",
}

TEXT_EXTENSIONS: set[str] = {
    ".py",
    ".ts",
    ".json",
    ".toml",
    ".md",
    ".sql",
    ".yml",
    ".yaml",
    ".sh",
    ".ps1",
    ".example",
    ".txt",
}

LOC_CHECK_EXTENSIONS: set[str] = {".py", ".ts", ".sql", ".yml", ".yaml"}

EMOJI_PATTERN = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _iter_repo_files() -> list[Path]:
    """Return all non-ignored repository files for static inspection."""
    matched: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if set(path.relative_to(REPO_ROOT).parts) & IGNORED_DIRS:
            continue
        matched.append(path)
    return matched


def _is_loc_exempt(path: Path) -> bool:
    """Determine if a file is exempt from the 250-LOC modularity limit."""
    if path.name in DOC_EXEMPT_NAMES:
        return True
    rel_posix = path.relative_to(REPO_ROOT).as_posix()
    return rel_posix.startswith(("docs/runbooks/", "docs/adr/"))


def test_clean_architecture_directories_exist() -> None:
    """Verify all mandatory Clean Architecture and documentation directories exist."""
    required_dirs = [
        "src/domain",
        "src/application",
        "src/infrastructure",
        "src/interfaces/cli",
        "src/interfaces/web",
        "sql",
        "docs/adr",
        "docs/runbooks",
    ]
    missing = [d for d in required_dirs if not (REPO_ROOT / d).is_dir()]
    assert not missing, f"[CRITICAL] Missing Clean Architecture directories: {missing}"


def test_gitignore_opsec_rules() -> None:
    """Verify .gitignore explicitly blocks sensitive local and runtime artifacts."""
    gitignore_path = REPO_ROOT / ".gitignore"
    assert gitignore_path.is_file(), "[CRITICAL] .gitignore file is missing at repository root"
    content = gitignore_path.read_text(encoding="utf-8")
    required_entries = [
        "hardware_audit_report.json",
        ".wslconfig.recommended",
        "MANUAL-MAESTRO-DARWIN-ES.md",
        "*.duckdb",
        ".env",
    ]
    missing = [entry for entry in required_entries if entry not in content]
    assert not missing, f"[OPSEC] Missing mandatory .gitignore protections: {missing}"


def test_zero_opsec_leaks_in_tracked_files() -> None:
    """Scan repository text files to guarantee zero local workstation identity leaks."""
    forbidden_tokens = [
        "Ce" + "X",
        "DESKTOP-" + "0620H8M",
        "D:\\" + "programs",
        "D:/" + "programs",
        "d:\\" + "programs",
        "d:/" + "programs",
        "C:\\" + "Users",
        "C:/" + "Users",
        "c:\\" + "users",
        "c:/" + "users",
    ]
    violations: list[str] = []
    for path in _iter_repo_files():
        if path.resolve() == SELF_PATH or path.name in IGNORED_OPSEC_FILES:
            continue
        if path.suffix not in TEXT_EXTENSIONS and path.name not in {".gitignore", "LICENSE"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in forbidden_tokens:
            if token in text:
                rel = path.relative_to(REPO_ROOT).as_posix()
                violations.append(f"{rel} leaked forbidden token '{token}'")
    assert not violations, f"[OPSEC] Workstation identity leaks detected: {violations}"


def test_zero_emojis_policy() -> None:
    """Verify zero Unicode emojis exist across all tracked source and doc files."""
    violations: list[str] = []
    for path in _iter_repo_files():
        if path.name in IGNORED_OPSEC_FILES:
            continue
        if path.suffix not in TEXT_EXTENSIONS and path.name not in {".gitignore", "LICENSE"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if EMOJI_PATTERN.search(text):
            violations.append(path.relative_to(REPO_ROOT).as_posix())
    assert not violations, f"[CRITICAL] Emoji policy violation in files: {violations}"


def test_asymmetric_loc_limit() -> None:
    """Enforce <= 250 LOC on all non-exempt code, SQL, and workflow files."""
    violations: list[str] = []
    for path in _iter_repo_files():
        if path.suffix not in LOC_CHECK_EXTENSIONS or _is_loc_exempt(path):
            continue
        line_count = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        if line_count > MAX_STANDARD_LOC:
            rel = path.relative_to(REPO_ROOT).as_posix()
            violations.append(f"{rel} ({line_count} LOC > {MAX_STANDARD_LOC})")
    assert not violations, f"[CRITICAL] Asymmetric LOC ceiling exceeded: {violations}"


def test_github_actions_workflow_and_node24_configured() -> None:
    """Verify quality gates workflow, Node 24 runtime, 4 gates, and .antigravity OPSEC ignore."""
    wf_path = REPO_ROOT / ".github" / "workflows" / "quality_gates.yml"
    assert wf_path.is_file(), "[CRITICAL] quality_gates.yml workflow is missing"
    content = wf_path.read_text(encoding="utf-8")
    assert 'FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"' in content, "[OPSEC] Node 24 env missing"
    for gate in ("Gate 1", "Gate 2", "Gate 3", "Gate 4"):
        assert gate in content, f"[CRITICAL] Missing {gate} definition in quality_gates.yml"
    res = subprocess.run(
        ["git", "check-ignore", ".antigravity"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"[OPSEC] .antigravity must be ignored by git: {res.stderr}"

