import json
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from slugify import slugify

KNOWLEDGE_DIR = Path.cwd() / "knowledge"
RAW_DIR = KNOWLEDGE_DIR / "raw"
TOPICS_DIR = KNOWLEDGE_DIR / "topics"
INDEX_PATH = KNOWLEDGE_DIR / "index.json"


@dataclass
class TopicEntry:
    name: str
    file: str
    sources: list[str] = field(default_factory=list)
    last_updated: str = ""


def _ensure_dirs():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> dict[str, TopicEntry]:
    if not INDEX_PATH.exists():
        return {}
    data = json.loads(INDEX_PATH.read_text())
    return {k: TopicEntry(**v) for k, v in data.items()}


def _save_index(index: dict[str, TopicEntry]):
    data = {k: v.__dict__ for k, v in index.items()}
    INDEX_PATH.write_text(json.dumps(data, indent=2))


def save_raw(url: str, title: str | None, content: str, base_path: Path) -> Path:
    _ensure_dirs()
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = slugify(title or url, max_length=60)
    path = base_path / f"{date}_{slug}.md"

    frontmatter = f"---\nurl: {url}\ntitle: {title or ''}\ndate: {date}\n---\n\n"
    path.write_text(frontmatter + content)
    return path


def list_topics() -> list[TopicEntry]:
    index = _load_index()
    return list(index.values())


def load_topic(name: str) -> str | None:
    index = _load_index()
    key = slugify(name)
    if key not in index:
        return None
    path = TOPICS_DIR / index[key].file
    if not path.exists():
        return None
    return path.read_text()


def save_topic(name: str, content: str, source_url: str):
    _ensure_dirs()
    index = _load_index()
    key = slugify(name)
    filename = f"{key}.md"

    (TOPICS_DIR / filename).write_text(content)

    now = datetime.now(timezone.utc).isoformat()
    if key in index:
        entry = index[key]
        if source_url not in entry.sources:
            entry.sources.append(source_url)
        entry.last_updated = now
    else:
        index[key] = TopicEntry(
            name=name,
            file=filename,
            sources=[source_url],
            last_updated=now,
        )
    _save_index(index)


def save_topic_raw(name: str, content: str):
    """Write topic file without updating index metadata."""
    _ensure_dirs()
    index = _load_index()
    key = slugify(name)
    if key not in index:
        return
    (TOPICS_DIR / index[key].file).write_text(content)


def get_sources(name: str) -> list[str] | None:
    index = _load_index()
    key = slugify(name)
    if key not in index:
        return None
    return index[key].sources


def is_url_ingested(url: str) -> str | None:
    """Return topic name if URL was already ingested, None otherwise."""
    index = _load_index()
    for entry in index.values():
        if url in entry.sources:
            return entry.name
    return None


def load_raw_by_url(url: str) -> str | None:
    """Find a raw file by its source URL and return its content (without frontmatter)."""
    if not RAW_DIR.exists():
        return None
    for path in RAW_DIR.glob("*.md"):
        text = path.read_text()
        if text.startswith("---"):
            end = text.index("---", 3)
            frontmatter = text[3:end]
            if f"url: {url}" in frontmatter:
                return text[end + 3:].strip()
    return None
