import sqlite3
from pathlib import Path
from pydantic import BaseModel
from pms.extract import extract_from_url
from pms.storage import save_raw

DB_PATH = ""

class Collection:
    def __init__(self, name: str, path:Path | str, description:str):
        self.name = name
        self.path = path
        self.description = description

    def add_from_url(self, url):
        try:
            doc = extract_from_url(url)
            path = save_raw(url=url, title=doc.title, content=doc.content, base_path=self.path)
            return f"Document saved at {path}"
        except Exception as e:
            raise ValueError(f"Error when adding the document: {e}")

    def __repr__(self):
        return f'Collection(name="{self.name}", path="{self.path}", description="{self.description}")'


    @property
    def files(self):
        return list(Path(self.path).glob("**/*.md"))
    

class Collections:

    def __init__(self, conn):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM collections WHERE name = ?", (name,)
        ).fetchone()
        return row is not None

    def get(self, name: str):
        row = self._conn.execute(
            "SELECT name, path, description FROM collections WHERE name = ?", (name,)
        ).fetchone()

        if not row:
            raise ValueError(f"Collection {name} does not exist")
        
        return Collection(
            name=row["name"],
            path=Path(row["path"]),
            description=row["description"]
        )

    
    def create(self, path, name, description):
        if self.exists(name):
            raise ValueError(f"Collection {name} already exists")
        
        resolved = Path(path).expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        
        with self._conn:
            self._conn.execute(
                "INSERT INTO collections (name, path, description) VALUES (?, ?, ?)",
                (name, str(resolved), description),
            )
        return Collection(
            name=name,
            path=Path(path).resolve(),
            description=description
        )


    def list(self):                
        with self._conn:
            rows = self._conn.execute(
                "SELECT name, path, description FROM collections"
            ).fetchall()

        return [
            Collection(
                name=row["name"],
                path=Path(row["path"]),
                description=row["description"]
            ) for row in rows
        ]


    def delete(self, name):
        if not self.get(name):
            raise ValueError(f"Collection {name} does not exist")
                
        with self._conn:
            self._conn.execute(
                "DELETE FROM collections WHERE name = ?",
                (name,),
            )
        return f"{name} deleted"

    