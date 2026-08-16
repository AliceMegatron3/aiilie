"""Models and low-cost hierarchical retrieval for local knowledge libraries.

The historical material folder is treated as a catalog, not as one large
prompt.  A root index selects branch indexes, and only then are document
excerpts opened.  This keeps retrieval auditable and limits disk/token work.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from core.path_resolver import get_bundle_dir

logger = logging.getLogger(__name__)


class LibraryManifestEntry(BaseModel):
    source_document_id: str
    library_id: str
    relative_path: str
    title: str = ""
    directory: str = ""
    index_type: str = "document"
    parent_source_document_id: str | None = None
    children_source_document_ids: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    size_bytes: int = 0
    modified_at: float | None = None


class LibraryManifest(BaseModel):
    manifest_version: str = "1.0"
    library_id: str
    library_name: str
    root_path: str
    root_source_document_id: str
    generated_at: str
    entries: list[LibraryManifestEntry] = Field(default_factory=list)


class LibraryDocumentHit(BaseModel):
    source_document_id: str
    relative_path: str
    title: str
    index_type: str
    score: float = 0.0
    excerpt: str = ""
    matched_terms: list[str] = Field(default_factory=list)


class LibrarySearchResult(BaseModel):
    query: str = ""
    hits: list[LibraryDocumentHit] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)


class LibraryAuditReport(BaseModel):
    """Manifest integrity findings with actionable locations and IDs."""

    entry_count: int = 0
    missing_paths: list[str] = Field(default_factory=list)
    orphaned_entries: list[str] = Field(default_factory=list)
    duplicate_ids: list[str] = Field(default_factory=list)

    @property
    def has_duplicate_ids(self) -> bool:
        return bool(self.duplicate_ids)


class LibraryCommand(BaseModel):
    """Stable author-to-library command protocol.

    ``payload`` is deliberately open because card subclasses evolve, while
    the action and routing fields stay stable for clients and agents.
    """

    action: Literal[
        "retrieve_material", "retrieve_card", "create_card", "maintain_card",
        "link_cards", "audit",
    ]
    query: str = ""
    card_id: str | None = None
    target_card_id: str | None = None
    relation_type: str = "related_to"
    filters: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


def parse_library_command(command: str, options: dict[str, Any] | None = None) -> LibraryCommand | None:
    """Parse the structured protocol first, then a small explicit NL subset."""
    options = options or {}
    raw = options.get("library_command")
    if isinstance(raw, LibraryCommand):
        return raw
    if isinstance(raw, dict):
        return LibraryCommand.model_validate(raw)
    if isinstance(options.get("library_action"), str):
        return LibraryCommand(
            action=options["library_action"], query=str(options.get("query", command)),
            card_id=options.get("card_id"), target_card_id=options.get("target_card_id"),
            relation_type=str(options.get("relation_type", "related_to")),
            filters=dict(options.get("filters", {})), payload=dict(options.get("payload", {})),
        )
    text = command.strip()
    patterns = (
        ("audit", ("审计资料库", "审计书库", "检查资料索引")),
        ("create_card", ("创建卡片", "新建卡片")),
        ("maintain_card", ("维护卡片", "更新卡片", "修改卡片")),
        ("link_cards", ("关联卡片", "连接卡片")),
        ("retrieve_card", ("调用卡片", "查找卡片", "检索卡片")),
        ("retrieve_material", ("调用资料", "查资料", "检索资料", "调用历史资料")),
    )
    for action, keywords in patterns:
        if any(keyword in text for keyword in keywords):
            query = text
            for keyword in keywords:
                query = query.replace(keyword, "", 1).strip(" ：:，,")
            return LibraryCommand(action=action, query=query)
    return None


class WorldContext(BaseModel):
    """Structured context contract consumed by the model dispatcher."""

    must_include: list[dict[str, Any]] = Field(default_factory=list)
    should_include: list[dict[str, Any]] = Field(default_factory=list)
    optional_reference: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)

    def as_prompt(self) -> str:
        sections: list[str] = []
        for label, values in (
            ("必须遵守的硬规则", self.must_include),
            ("应优先参考的卡片", self.should_include),
            ("可选资料摘录", self.optional_reference),
        ):
            if not values:
                continue
            lines = [label]
            for value in values:
                content = value.get("content") or value.get("excerpt") or value.get("summary") or ""
                source = value.get("source_document_id") or value.get("card_id") or "unknown"
                lines.append(f"- [{source}] {content}")
            sections.append("\n".join(lines))
        return "【分层资料上下文】\n" + "\n\n".join(sections) + "\n---\n" if sections else ""


def _stable_document_id(library_id: str, relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.replace("\\", "/").encode("utf-8")).hexdigest()[:16]
    return f"{library_id}:{digest}"


def _title_from_markdown(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:30]:
            match = re.match(r"^\s*#+\s+(.+?)\s*$", line)
            if match:
                return match.group(1).strip().strip("# ")
    except OSError:
        pass
    return path.stem


_DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "time": ("时间", "编年", "年表", "时代", "疫病"),
    "geography": ("地利", "地理", "区划", "蜀道", "交通"),
    "society": ("人和", "阶层", "社会", "族群", "利益", "民俗"),
    "institution": ("制度", "数字", "货币", "税", "度量"),
    "technology": ("科技", "技术", "物质", "医疗", "衣冠", "饮食"),
    "evidence": ("正史", "证据", "核验", "研究", "参考书"),
    "emotion": ("情绪", "人设", "七情", "人物"),
    "pressure": ("压力", "灾荒", "战争", "逃难", "环境"),
    "style": ("写作", "著作", "场景", "写景", "技巧"),
}


def _domains(text: str) -> list[str]:
    return [domain for domain, hints in _DOMAIN_HINTS.items() if any(hint in text for hint in hints)]


def _keywords(text: str) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{1,}", text)
    return list(dict.fromkeys(tokens))[:30]


class LibraryManifestScanner:
    """Build a stable source registry from a folder of Markdown files."""

    def __init__(self, root: Path | None = None, library_id: str = "historical_materials") -> None:
        self.root = Path(root or (get_bundle_dir() / "历史资料")).resolve()
        self.library_id = library_id

    def scan(self) -> LibraryManifest:
        if not self.root.exists():
            raise FileNotFoundError(f"资料库目录不存在: {self.root}")
        paths = sorted(self.root.rglob("*.md"), key=lambda path: path.as_posix())
        relative_paths = {path: path.relative_to(self.root).as_posix() for path in paths}
        ids = {path: _stable_document_id(self.library_id, relative_paths[path]) for path in paths}
        by_directory: dict[str, Path] = {}
        for path in paths:
            relative = relative_paths[path]
            directory = Path(relative).parent.as_posix()
            if path.name.startswith("00-") or path.name.startswith("00_") or "索引" in path.name:
                by_directory.setdefault(directory, path)

        root_path = next((path for path in paths if relative_paths[path] == "00-索引与调用.md"), paths[0] if paths else None)
        if root_path is None:
            raise ValueError(f"资料库没有 Markdown 文件: {self.root}")

        entries: list[LibraryManifestEntry] = []
        for path in paths:
            relative = relative_paths[path]
            directory = Path(relative).parent.as_posix()
            is_index = path.name.startswith("00-") or path.name.startswith("00_") or "索引" in path.name
            if path == root_path:
                index_type = "root"
                parent_id = None
            elif is_index:
                index_type = "branch"
                parent_directory = Path(directory).parent.as_posix()
                parent_index = by_directory.get(parent_directory)
                parent_id = ids.get(parent_index, ids[root_path])
            else:
                index_type = "document"
                parent_path = by_directory.get(directory)
                parent_id = ids.get(parent_path, ids[root_path])
            text = f"{relative} {path.stem}"
            try:
                stat = path.stat()
                size_bytes, modified_at = stat.st_size, stat.st_mtime
            except OSError:
                size_bytes, modified_at = 0, None
            entries.append(LibraryManifestEntry(
                source_document_id=ids[path],
                library_id=self.library_id,
                relative_path=relative,
                title=_title_from_markdown(path),
                directory=directory if directory != "." else "",
                index_type=index_type,
                parent_source_document_id=parent_id,
                domains=_domains(text),
                keywords=_keywords(text),
                size_bytes=size_bytes,
                modified_at=modified_at,
            ))

        entry_by_id = {entry.source_document_id: entry for entry in entries}
        for entry in entries:
            if entry.parent_source_document_id and entry.parent_source_document_id in entry_by_id:
                entry_by_id[entry.parent_source_document_id].children_source_document_ids.append(entry.source_document_id)
        return LibraryManifest(
            library_id=self.library_id,
            library_name=self.root.name,
            root_path=self.root.name,
            root_source_document_id=ids[root_path],
            generated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            entries=entries,
        )

    def write(self, output_path: Path | None = None) -> Path:
        target = Path(output_path or (self.root.parent / "library_manifest.json"))
        manifest = self.scan()
        target.write_text(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        return target


class LibraryCatalog:
    """Cached manifest access and staged document retrieval."""

    def __init__(self, manifest_path: Path | None = None) -> None:
        self.manifest_path = Path(manifest_path or (get_bundle_dir() / "library_manifest.json"))
        self._manifest: LibraryManifest | None = None

    def manifest(self) -> LibraryManifest:
        if self._manifest is None:
            if self.manifest_path.exists():
                self._manifest = LibraryManifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))
            else:
                self._manifest = LibraryManifestScanner().scan()
        return self._manifest

    @staticmethod
    def _score(entry: LibraryManifestEntry, query: str) -> tuple[float, list[str]]:
        if not query.strip():
            return 0.0, []
        terms = _keywords(query)
        haystack = " ".join((entry.title, entry.relative_path, entry.directory, *entry.domains, *entry.keywords))
        matched = [term for term in terms if term in haystack]
        return float(len(matched) * 3), matched

    def retrieve(self, query: str = "", limit: int = 8, include_excerpts: bool = True) -> LibrarySearchResult:
        manifest = self.manifest()
        root = next(entry for entry in manifest.entries if entry.index_type == "root")
        trace: list[dict[str, Any]] = [{"stage": "root_index", "source_document_id": root.source_document_id, "loaded": True}]
        branches = [entry for entry in manifest.entries if entry.index_type == "branch"]
        scored_branches: list[tuple[float, LibraryManifestEntry, list[str]]] = []
        for branch in branches:
            score, matched = self._score(branch, query)
            descendants = [entry for entry in manifest.entries if entry.directory == branch.directory and entry.index_type == "document"]
            descendant_score = max((self._score(entry, query)[0] for entry in descendants), default=0.0)
            scored_branches.append((max(score, descendant_score), branch, matched))
        selected = [item for item in sorted(scored_branches, key=lambda item: item[0], reverse=True) if item[0] > 0]
        if not selected:
            selected = sorted(scored_branches, key=lambda item: item[1].relative_path)[: min(6, len(scored_branches))]
        selected = selected[: max(1, min(len(selected), 8))]
        for score, branch, matched in selected:
            trace.append({"stage": "branch_index", "source_document_id": branch.source_document_id, "score": score, "matched_terms": matched})

        candidates: list[tuple[float, LibraryManifestEntry, list[str]]] = []
        selected_ids = {branch.source_document_id for _, branch, _ in selected}
        selected_dirs = {branch.directory for _, branch, _ in selected}
        for entry in manifest.entries:
            if entry.index_type != "document":
                continue
            # Root-level books (01-10) are direct children of the root index;
            # nested documents are reached through their selected branch index.
            if entry.directory and entry.parent_source_document_id not in selected_ids:
                continue
            if entry.directory and entry.directory not in selected_dirs:
                continue
            score, matched = self._score(entry, query)
            candidates.append((score, entry, matched))
        candidates.sort(key=lambda item: (item[0], item[1].relative_path), reverse=True)
        hits: list[LibraryDocumentHit] = []
        root_dir = Path(manifest.root_path).parent
        for score, entry, matched in candidates[: max(1, min(int(limit), 50))]:
            excerpt = ""
            if include_excerpts:
                path = self.manifest_path.parent / manifest.root_path / entry.relative_path
                excerpt = self._excerpt(path, query)
            trace.append({"stage": "document", "source_document_id": entry.source_document_id, "relative_path": entry.relative_path, "score": score})
            hits.append(LibraryDocumentHit(source_document_id=entry.source_document_id, relative_path=entry.relative_path, title=entry.title, index_type=entry.index_type, score=score, excerpt=excerpt, matched_terms=matched))
        return LibrarySearchResult(query=query, hits=hits, trace=trace)

    def audit(self) -> LibraryAuditReport:
        """Check manifest paths and hierarchy without opening the card index."""
        manifest = self.manifest()
        known_ids = {entry.source_document_id for entry in manifest.entries}
        missing_paths = [
            entry.relative_path
            for entry in manifest.entries
            if not (self.manifest_path.parent / manifest.root_path / entry.relative_path).exists()
        ]
        orphaned_entries = [
            entry.source_document_id
            for entry in manifest.entries
            if entry.parent_source_document_id
            and entry.parent_source_document_id not in known_ids
        ]
        id_counts = Counter(entry.source_document_id for entry in manifest.entries)
        duplicate_ids = sorted(source_id for source_id, count in id_counts.items() if count > 1)
        return LibraryAuditReport(
            entry_count=len(manifest.entries),
            missing_paths=missing_paths,
            orphaned_entries=orphaned_entries,
            duplicate_ids=duplicate_ids,
        )

    @staticmethod
    def _excerpt(path: Path, query: str, max_chars: int = 1200) -> str:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        terms = _keywords(query)
        for index, line in enumerate(lines):
            if terms and any(term in line for term in terms):
                start = max(0, index - 2)
                return "\n".join(lines[start:index + 8])[:max_chars]
        return "\n".join(lines[:12])[:max_chars]


class WorldContextBuilder:
    """Join filtered cards and staged source excerpts into one context."""

    def __init__(self, indexer: Any, catalog: LibraryCatalog | None = None) -> None:
        self.indexer = indexer
        self.catalog = catalog or LibraryCatalog()

    async def build(self, query: str = "", book_ids: Iterable[str] | None = None, **filters: Any) -> WorldContext:
        document_limit = int(filters.pop("document_limit", 6))
        cards: list[dict[str, Any]] = []
        source_books = list(book_ids or [])
        if source_books:
            for book_id in source_books:
                cards.extend(await self.indexer.search_cards(keyword=query or None, source_book=book_id, **filters))
        # A project binding is the explicit permission to load book cards.
        # Document retrieval can still use the root catalog below, but an
        # unbound project must not accidentally scan every card in the system.
        cards.sort(key=lambda card: (card.get("status") == "approved", card.get("rule_strength") == "hard", card.get("utility_score", 0)), reverse=True)
        must = [card for card in cards if card.get("status") == "approved" and card.get("rule_strength") == "hard"]
        should = [card for card in cards if card not in must and card.get("status") in {"approved", "reviewed"}]
        optional = [card for card in cards if card not in must and card not in should]
        result = WorldContext(must_include=must[:50], should_include=should[:50], optional_reference=optional[:20])
        if query:
            docs = self.catalog.retrieve(query, limit=document_limit)
            result.optional_reference.extend(hit.model_dump(mode="json") for hit in docs.hits)
            result.trace.extend(docs.trace)
        result.trace.append({"stage": "cards", "count": len(cards), "must": len(must), "should": len(should), "optional": len(optional)})
        return result


__all__ = [
    "LibraryAuditReport", "LibraryCatalog", "LibraryDocumentHit", "LibraryManifest", "LibraryManifestEntry",
    "LibraryManifestScanner", "LibrarySearchResult", "WorldContext", "WorldContextBuilder",
]