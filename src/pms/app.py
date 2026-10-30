import re
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pms.kb import KnowledgeBase

app = FastAPI(title="Syllabus")
kb = KnowledgeBase()

UI_PATH = Path(__file__).parent / "ui.html"


class CollectionCreate(BaseModel):
    name: str
    path: str
    description: str = ""


class DocumentCreate(BaseModel):
    url: str
    collection: str


@app.get("/")
def index():
    return FileResponse(UI_PATH)


@app.get("/api/collections")
def list_collections():
    return [
        {"name": c.name, "path": str(c.path), "description": c.description}
        for c in kb.collections.list()
    ]


@app.post("/api/collections")
def create_collection(body: CollectionCreate):
    try:
        col = kb.collections.create(
            path=body.path, name=body.name, description=body.description
        )
        return {"name": col.name, "path": str(col.path), "description": col.description}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/documents")
def list_documents(collection: str | None = None):
    with kb._conn:
        if collection and collection != "all":
            rows = kb._conn.execute(
                "SELECT id, collection, path, title FROM documents WHERE collection = ? ORDER BY id DESC",
                (collection,),
            ).fetchall()
        else:
            rows = kb._conn.execute(
                "SELECT id, collection, path, title FROM documents ORDER BY collection, id DESC"
            ).fetchall()
    return [
        {
            "id": r["id"],
            "collection": r["collection"],
            "path": r["path"],
            "title": r["title"],
        }
        for r in rows
    ]


@app.post("/api/documents")
def add_document(body: DocumentCreate):
    try:
        col = kb.collections.get(body.collection)
        msg = col.add_from_url(body.url)
        return {"message": msg}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/sync")
def sync_status():
    to_add, to_delete, to_update = kb.check_sync_status()
    return {
        "to_add": len(to_add),
        "to_update": len(to_update),
        "to_delete": len(to_delete),
    }


@app.post("/api/sync")
def run_sync():
    to_add, to_delete, to_update = kb.check_sync_status()
    kb.sync()
    return {
        "added": len(to_add),
        "updated": len(to_update),
        "deleted": len(to_delete),
    }


@app.get("/api/search")
def search(q: str = "", k: int = 10, collection: str | None = None):
    if not q.strip():
        return []
    fetch_k = k * 5 if (collection and collection != "all") else k
    hits = kb.search(q, k=fetch_k)
    if isinstance(hits, str):
        raise HTTPException(status_code=500, detail=hits)
    if collection and collection != "all":
        hits = [h for h in hits if h.collection == collection][:k]
    return hits


@app.get("/api/document")
def get_document(path: str):
    with kb._conn:
        row = kb._conn.execute(
            "SELECT collection, path, title FROM documents WHERE path = ?",
            (path,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = Path(row["path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    raw = file_path.read_text(encoding="utf-8")
    url_match = re.search(r"^url:\s*(.+)$", raw, flags=re.MULTILINE)
    date_match = re.search(r"^date:\s*(.+)$", raw, flags=re.MULTILINE)
    content = re.sub(r"^---\n.*?\n---\n?", "", raw, count=1, flags=re.DOTALL).strip()

    return {
        "title": row["title"],
        "collection": row["collection"],
        "path": row["path"],
        "url": url_match.group(1).strip() if url_match else None,
        "date": date_match.group(1).strip() if date_match else None,
        "content": content,
    }

