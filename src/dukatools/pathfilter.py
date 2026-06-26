"""Shared include/exclude path filtering helpers."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


def flatten_groups(groups: Sequence[Sequence[str]] | None) -> list[str]:
    values: list[str] = []
    for group in groups or []:
        values.extend(group)
    return values


def posix_rel(path: Path, root: Path) -> str:
    rel = lex_abs(path).relative_to(lex_abs(root))
    if rel == Path("."):
        return ""
    return rel.as_posix()


def lex_abs(path: Path | str) -> Path:
    """Return an absolute, normalized path without resolving symlink targets."""
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def ancestor_rel_paths(rel_posix: str) -> list[str]:
    if not rel_posix:
        return [""]
    parts = rel_posix.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


@dataclass(frozen=True)
class ExactPathRule:
    raw: str
    path: Path

    def matches(self, candidate: Path) -> bool:
        candidate = lex_abs(candidate)
        target = lex_abs(self.path)
        if candidate == target:
            return True
        try:
            candidate.relative_to(target)
            return True
        except ValueError:
            return False


@dataclass(frozen=True)
class PathFilter:
    """Select paths using exact path rules and glob-style path patterns.

    Exact rules are absolute paths internally. Relative exact rules are resolved
    against `root`, which is the directory the current utility operates on.

    Pattern rules use POSIX-style relative paths inside `root`. Pattern matching
    is also tested against ancestors so that a pattern matching a directory
    applies to the directory subtree.
    """

    root: Path
    include_paths: tuple[ExactPathRule, ...] = ()
    exclude_paths: tuple[ExactPathRule, ...] = ()
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()

    @classmethod
    def build(
        cls,
        root: Path | str,
        *,
        include_paths: Iterable[str] = (),
        exclude_paths: Iterable[str] = (),
        include_patterns: Iterable[str] = (),
        exclude_patterns: Iterable[str] = (),
    ) -> "PathFilter":
        root_path = lex_abs(root)
        return cls(
            root=root_path,
            include_paths=tuple(_build_exact_rules(root_path, include_paths)),
            exclude_paths=tuple(_build_exact_rules(root_path, exclude_paths)),
            include_patterns=tuple(p for p in include_patterns if p),
            exclude_patterns=tuple(p for p in exclude_patterns if p),
        )

    @property
    def has_includes(self) -> bool:
        return bool(self.include_paths or self.include_patterns)

    def rel(self, path: Path | str) -> str:
        return posix_rel(Path(path), self.root)

    def is_excluded(self, path: Path | str) -> bool:
        candidate = Path(path)
        if _matches_exact(candidate, self.exclude_paths):
            return True
        return _matches_patterns(self.rel(candidate), self.exclude_patterns)

    def is_included(self, path: Path | str) -> bool:
        if not self.has_includes:
            return True
        candidate = Path(path)
        if _matches_exact(candidate, self.include_paths):
            return True
        return _matches_patterns(self.rel(candidate), self.include_patterns)

    def selects(self, path: Path | str) -> bool:
        return self.is_included(path) and not self.is_excluded(path)

    def is_ancestor_of_include(self, path: Path | str) -> bool:
        """Return True when `path` is needed to reach an exact include path."""
        if not self.include_paths:
            return False
        candidate = lex_abs(path)
        for rule in self.include_paths:
            try:
                lex_abs(rule.path).relative_to(candidate)
                return True
            except ValueError:
                continue
        return False


def _build_exact_rules(root: Path, values: Iterable[str]) -> list[ExactPathRule]:
    rules: list[ExactPathRule] = []
    for raw in values:
        if not raw:
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = root / p
        rules.append(ExactPathRule(raw=raw, path=p))
    return rules


def _matches_exact(candidate: Path, rules: Sequence[ExactPathRule]) -> bool:
    return any(rule.matches(candidate) for rule in rules)


def _matches_patterns(rel_posix: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return False
    for rel in ancestor_rel_paths(rel_posix):
        for pattern in patterns:
            if fnmatch.fnmatchcase(rel, pattern):
                return True
    return False
