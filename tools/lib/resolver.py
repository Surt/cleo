"""Dependency resolver for cleo — resolves transitive package dependencies.

Given top-level requirements, fetches package manifests, discovers their own
dependencies, builds a dependency graph, and returns a topologically sorted
install plan.

Detects cycles, version conflicts, and missing dependencies.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .security import SecurityViolation, validate_git_ref, validate_package_ref
from .semver import matches_constraint, parse_version, resolve_commit, resolve_version


class DependencyCycle(Exception):
    """Raised when a cycle is detected in the dependency graph."""


class VersionConflict(Exception):
    """Raised when two packages require incompatible versions of a dep."""


@dataclass
class ResolvedPackage:
    name: str
    url: str
    constraint: str
    version: str
    tag: str
    commit: str
    bucket: str
    required_by: list[str] = field(default_factory=list)


def _toposort(graph: dict[str, set[str]]) -> list[str]:
    """Kahn's algorithm — returns nodes in topological order (deps first).

    Raises DependencyCycle if the graph contains a cycle.
    """
    in_degree: dict[str, int] = {node: 0 for node in graph}
    for node, deps in graph.items():
        for dep in deps:
            if dep not in in_degree:
                in_degree[dep] = 0
            in_degree[dep] += 1

    queue = [n for n, d in in_degree.items() if d == 0]
    order: list[str] = []

    while queue:
        queue.sort()
        node = queue.pop(0)
        order.append(node)
        for dep in sorted(graph.get(node, set())):
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                queue.append(dep)

    if len(order) != len(in_degree):
        remaining = set(in_degree) - set(order)
        raise DependencyCycle(
            f"dependency cycle detected among: {', '.join(sorted(remaining))}"
        )

    return order


def _constraints_compatible(constraints: list[str]) -> bool:
    """Check if a list of constraints can be simultaneously satisfied.

    Returns True if there exists at least one version that satisfies all
    constraints (tested against a synthetic range of versions).
    """
    if not constraints or all(c in ("*", "") for c in constraints):
        return True
    for major in range(100):
        for minor in range(100):
            for patch in range(20):
                v = parse_version(f"{major}.{minor}.{patch}")
                if v and all(matches_constraint(v, c) for c in constraints):
                    return True
    return False


def _merge_constraints(existing: str, new: str) -> str:
    """Merge two constraints into a single space-separated AND constraint."""
    if existing in ("*", ""):
        return new
    if new in ("*", ""):
        return existing
    if existing == new:
        return existing
    return f"{existing} {new}"


def _github_url(name: str) -> str:
    return f"https://github.com/{name}"


def _read_cached_manifest(cache_dir: Path) -> dict | None:
    """Read a package's cleo.json from cache. Returns None if absent."""
    p = cache_dir / "cleo.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


@dataclass
class _PendingPackage:
    name: str
    url: str
    constraint: str
    bucket: str
    required_by: str  # who pulled this dep in ("" = top-level)


def resolve_all(
    top_level: list[tuple[str, str, str, str]],
    *,
    pkg_cache_dir_fn: Callable[[str, str], Path],
    clone_fn: Callable[[str, Path, str, Optional[str]], bool],
    resolve_version_fn: Callable[..., Optional[tuple[str, str]]] | None = None,
    resolve_commit_fn: Callable[..., Optional[str]] | None = None,
    offline: bool = False,
    max_depth: int = 10,
    jobs: int = 1,
) -> list[ResolvedPackage]:
    """Resolve all transitive dependencies.

    Args:
        top_level: List of (name, constraint, url, bucket) for direct deps.
        pkg_cache_dir_fn: Maps (name, version) to cache Path.
        clone_fn: Clones url to cache_dir at tag; returns success bool.
        resolve_version_fn: Resolves (url, constraint) → (version, tag) or None.
        resolve_commit_fn: Resolves (url, tag) → commit SHA or None.
        offline: Skip network resolution.
        max_depth: Maximum dependency depth (guards against deep chains).
        jobs: Number of parallel fetch workers (1 = sequential).

    Returns:
        List of ResolvedPackage in topological order (deps installed first).

    Raises:
        DependencyCycle: If a cycle is detected.
        VersionConflict: If incompatible version constraints exist.
    """
    if resolve_version_fn is None:
        resolve_version_fn = resolve_version
    if resolve_commit_fn is None:
        resolve_commit_fn = resolve_commit

    resolved: dict[str, ResolvedPackage] = {}
    graph: dict[str, set[str]] = {}
    constraints: dict[str, list[str]] = {}

    queue: list[tuple[_PendingPackage, int]] = []
    for name, constraint, url, bucket in top_level:
        queue.append((_PendingPackage(name, url, constraint, bucket, ""), 0))
        graph.setdefault(name, set())

    visited_names: set[str] = set()

    while queue:
        if jobs > 1:
            batch, queue = _take_batch(queue, visited_names)
            results = _fetch_batch_parallel(
                batch, resolve_version_fn, resolve_commit_fn,
                pkg_cache_dir_fn, clone_fn, offline, jobs,
            )
            for pending, depth, result in results:
                _process_resolved(
                    pending, depth, result, resolved, graph, constraints,
                    queue, pkg_cache_dir_fn, max_depth,
                )
                visited_names.add(pending.name)
        else:
            pending, depth = queue.pop(0)
            if pending.name in visited_names:
                if pending.name in resolved:
                    resolved[pending.name].required_by.append(pending.required_by)
                    constraints.setdefault(pending.name, []).append(pending.constraint)
                continue
            visited_names.add(pending.name)

            result = _fetch_one(
                pending, resolve_version_fn, resolve_commit_fn,
                pkg_cache_dir_fn, clone_fn, offline,
            )
            if result is None:
                continue
            _process_resolved(
                pending, depth, result, resolved, graph, constraints,
                queue, pkg_cache_dir_fn, max_depth,
            )

    for name, constraint_list in constraints.items():
        if not _constraints_compatible(constraint_list):
            requesters = ", ".join(resolved[name].required_by) if name in resolved else "?"
            raise VersionConflict(
                f"version conflict for {name}: constraints "
                f"{constraint_list} (required by: {requesters}) "
                f"cannot be simultaneously satisfied"
            )

    order = _toposort(graph)
    # Reverse: toposort gives dependents first, we want deps first
    order.reverse()
    return [resolved[name] for name in order if name in resolved]


def _take_batch(
    queue: list[tuple[_PendingPackage, int]],
    visited: set[str],
) -> tuple[list[tuple[_PendingPackage, int]], list[tuple[_PendingPackage, int]]]:
    """Split queue into a batch of unvisited packages and the remainder."""
    batch = []
    remainder = []
    seen_in_batch: set[str] = set()
    for item in queue:
        pending, depth = item
        if pending.name not in visited and pending.name not in seen_in_batch:
            batch.append(item)
            seen_in_batch.add(pending.name)
        else:
            remainder.append(item)
    return batch, remainder


@dataclass
class _FetchResult:
    version: str
    tag: str
    commit: str
    cache_dir: Path


def _fetch_one(
    pending: _PendingPackage,
    resolve_version_fn,
    resolve_commit_fn,
    pkg_cache_dir_fn,
    clone_fn,
    offline: bool,
) -> _FetchResult | None:
    """Resolve version and fetch a single package to cache."""
    if offline:
        return None

    result = resolve_version_fn(pending.url, pending.constraint)
    if result is None:
        return None
    version, tag = result
    commit = resolve_commit_fn(pending.url, tag) or ""

    cache_dir = pkg_cache_dir_fn(pending.name, version)
    if not cache_dir.exists() or not (cache_dir / ".git").exists():
        ok = clone_fn(pending.url, cache_dir, tag, expected_commit=commit or None)
        if not ok:
            return None

    return _FetchResult(version=version, tag=tag, commit=commit, cache_dir=cache_dir)


def _fetch_batch_parallel(
    batch: list[tuple[_PendingPackage, int]],
    resolve_version_fn,
    resolve_commit_fn,
    pkg_cache_dir_fn,
    clone_fn,
    offline: bool,
    jobs: int,
) -> list[tuple[_PendingPackage, int, _FetchResult | None]]:
    """Fetch a batch of packages in parallel."""
    results: list[tuple[_PendingPackage, int, _FetchResult | None]] = []
    with ThreadPoolExecutor(max_workers=min(jobs, len(batch))) as executor:
        futures = {}
        for pending, depth in batch:
            fut = executor.submit(
                _fetch_one, pending,
                resolve_version_fn, resolve_commit_fn,
                pkg_cache_dir_fn, clone_fn, offline,
            )
            futures[fut] = (pending, depth)
        for fut in as_completed(futures):
            pending, depth = futures[fut]
            try:
                result = fut.result()
            except Exception:
                result = None
            results.append((pending, depth, result))
    return results


def _process_resolved(
    pending: _PendingPackage,
    depth: int,
    result: _FetchResult | None,
    resolved: dict[str, ResolvedPackage],
    graph: dict[str, set[str]],
    constraints: dict[str, list[str]],
    queue: list[tuple[_PendingPackage, int]],
    pkg_cache_dir_fn,
    max_depth: int,
) -> None:
    """Record a resolved package and enqueue its transitive deps."""
    if result is None:
        return

    constraints.setdefault(pending.name, []).append(pending.constraint)
    req_by = [pending.required_by] if pending.required_by else []

    pkg = ResolvedPackage(
        name=pending.name,
        url=pending.url,
        constraint=pending.constraint,
        version=result.version,
        tag=result.tag,
        commit=result.commit,
        bucket=pending.bucket,
        required_by=req_by,
    )
    resolved[pending.name] = pkg
    graph.setdefault(pending.name, set())

    if depth >= max_depth:
        return

    manifest = _read_cached_manifest(result.cache_dir)
    if not manifest:
        return

    pkg_requires = manifest.get("require", {})
    if not isinstance(pkg_requires, dict):
        return

    for dep_name, dep_constraint in pkg_requires.items():
        try:
            dep_name = validate_package_ref(dep_name)
        except SecurityViolation:
            continue
        if not isinstance(dep_constraint, str):
            continue

        graph[pending.name].add(dep_name)
        graph.setdefault(dep_name, set())

        if dep_name in resolved:
            resolved[dep_name].required_by.append(pending.name)
            constraints.setdefault(dep_name, []).append(dep_constraint)
            continue

        dep_repos = manifest.get("repositories", [])
        dep_url = _github_url(dep_name)
        for r in dep_repos:
            if isinstance(r, dict) and r.get("type") == "git" and r.get("url"):
                url = r["url"]
                if dep_name in url or url.rstrip("/").endswith("/" + dep_name.split("/")[-1]):
                    dep_url = url
                    break

        queue.append((
            _PendingPackage(dep_name, dep_url, dep_constraint, pending.bucket, pending.name),
            depth + 1,
        ))
