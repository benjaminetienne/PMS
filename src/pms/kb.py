import sqlite3, sqlite_vec
import re
import hashlib
from pathlib import Path
from pydantic import BaseModel
from fastembed import TextEmbedding
from pms.index import Collections
from pms.chunk import chunk_markdown
from pms.retrieval import HybridRetrieval


class KnowledgeBase:
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path(__file__).parent / "knowledgebase.db"
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # Init db if not exists
        self.init_db(self._conn)

        # Init collections
        self.collections = Collections(self._conn)

        self.embedding_model = TextEmbedding(
            model_name="BAAI/bge-small-en-v1.5"
        )
        print("The model BAAI/bge-small-en-v1.5 is ready to use.")

        self.retrieval = HybridRetrieval(self._conn, self.embedding_model)

    def init_db(self, conn):
        with conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute("PRAGMA foreign_keys = ON")

            conn.executescript("""

            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY, 
                name TEXT NOT NULL UNIQUE,
                path TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS documents(
                id INTEGER PRIMARY KEY, 
                collection TEXT NOT NULL REFERENCES collections(name) ON DELETE CASCADE, 
                path TEXT NOT NULL UNIQUE,
                title TEXT, 
                hash TEXT NOT NULL, 
                updated_at TEXT);

            CREATE TABLE IF NOT EXISTS chunks(
                id INTEGER PRIMARY KEY, 
                doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                seq INTEGER, 
                heading_path TEXT, 
                text TEXT, 
                start_line INTEGER, 
                end_line INTEGER);

            -- external content: FTS stores only the index, rowid == chunks.id
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                heading_path, 
                text, 
                content='chunks', 
                content_rowid='id',
                tokenize='porter unicode61');
            
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                embedding float[384] distance_metric=cosine);

            -- Triggers 
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN        
                -- Tell external-content FTS5 to un-index the old tokens            
                INSERT INTO chunks_fts(chunks_fts, rowid, heading_path, text)          
                VALUES ('delete', old.id, old.heading_path, old.text);                 
                                                                                    
                -- Delete the vector with the same rowid                            
                DELETE FROM chunks_vec WHERE rowid = old.id;                           
            END; 
            """)

    def check_sync_status(self):
        """ Checks documents stored in disk vs database """
        local_docs = {}
        for col in self.collections.list():
            for file_path in col.files:
                p = Path(file_path).resolve()
                content_hash = hashlib.sha256(p.read_bytes()).hexdigest()
                local_docs[str(p)] = {'collection': col.name, 'content_hash': content_hash}

        with self._conn:
            rows = self._conn.execute(
                "SELECT path, collection, hash FROM documents"
            ).fetchall()

        db_docs = {row["path"]: {'collection': row["collection"], 'content_hash': row["hash"]} for row in rows}

        local_paths = set(local_docs.keys())
        db_paths = set(db_docs.keys())

        to_add = [
            {
                'collection': local_docs[p]['collection'], 
                'path': Path(p), 
                'content_hash': local_docs[p]['content_hash']
            }
            for p in sorted(local_paths - db_paths)
        ]
        to_delete = sorted(db_paths - local_paths)
        to_update = [
            {
                'collection': local_docs[p]['collection'], 
                'path': Path(p), 
                'content_hash': local_docs[p]['content_hash']
            }
            for p in sorted(local_paths & db_paths)
            if local_docs[p]['content_hash'] != db_docs[p]['content_hash']
        ]
        print(
            f"Sync status: +{len(to_add)} new, ~{len(to_update)} modified, -{len(to_delete)} deleted"
        )
        return to_add, to_delete, to_update

    def _add_file(self, collection_name: str, filename: str, content_hash: str):
        """ Add files that are stored locally but not in db """
        with Path(filename).open() as f:
            md = f.read()

        content = re.sub(r'^---\n.*?\n---\n?', '', md, count=1, flags=re.DOTALL).strip()
        title = re.findall(r'title: (.*)\n', md)

        if title:
            title = title[0]
        else:
            title = "Unknown"

        if not collection_name:
            # documents.collection is a NOT NULL foreign key; fail loudly rather
            # than let a bad row through.
            raise ValueError(f"collection_name is required to index {filename!r}")

        chunks = chunk_markdown(content, title=title)
        vectors = list(self.embedding_model.passage_embed([c.embed_text for c in chunks]))


        with self._conn:
            # Add document
            doc_id = self._conn.execute(
                "INSERT INTO documents(collection, path, title, hash) VALUES (?,?,?,?)",
                (collection_name, str(filename), title, content_hash),
            ).lastrowid
            
            for chunk, vec in zip(chunks, vectors):
                # Add chunks
                chunk_id = self._conn.execute(
                    "INSERT INTO chunks(doc_id, seq, heading_path, text, start_line, end_line)"
                    " VALUES (?,?,?,?,?,?)",
                    (doc_id, chunk.seq, chunk.heading_path, chunk.text, chunk.start_line, chunk.end_line),
                ).lastrowid

                # Add to FTS5
                self._conn.execute(
                    "INSERT INTO chunks_fts(rowid, heading_path, text) VALUES (?,?,?)",
                    (chunk_id, chunk.heading_path, chunk.text),
                )

                # Add embeddings
                self._conn.execute(
                    "INSERT INTO chunks_vec(rowid, embedding) VALUES (?,?)",
                    (chunk_id, sqlite_vec.serialize_float32(vec)),
                )

            self._conn.commit()


    def _delete_file(self, filename: str | Path):
        """ Delete files that are stored in the db but not locally """
        with self._conn:
            self._conn.execute("""
                DELETE FROM documents WHERE path = ?
            """, (str(filename),)
            )


    def _update_file(self, collection_name: str, filename: str | Path, content_hash: str):
        """ Update files that are stored in the db but have changed locally """
        self._delete_file(filename)
        self._add_file(collection_name=collection_name, filename=filename, content_hash=content_hash)

         
    def sync(self):
        to_add, to_delete, to_update = self.check_sync_status()
        if to_add:
            for file in to_add:
                try:
                    self._add_file(
                        collection_name=file['collection'], 
                        filename=file['path'], 
                        content_hash=file['content_hash']
                    )
                    print(f"✔ {file['path']} was added")
                except Exception as e:
                    print(f"✘ {file['path']} was not added. Error: {e} ")

        if to_update:
            for file in to_update:
                try:
                    self._update_file(
                        collection_name=file['collection'], 
                        filename=file['path'], 
                        content_hash=file['content_hash']
                    )
                    print(f"✔ {file['path']} was updated")
                except Exception as e:
                    print(f"✘ {file['path']} was not updated. Error: {e} ")

        if to_delete:
            for file in to_delete:
                try:
                    self._delete_file(
                        filename=file
                    )
                    print(f"✔ {file} was deleted")
                except Exception as e:
                    print(f"✘ {file} was not deleted. Error: {e} ")

    def rebuild_vec(self):
        """Repack the sqlite-vec table to reclaim wasted space.

        sqlite-vec stores vectors in fixed 1024-slot blocks, each pre-allocated
        at full size (~1.5MB for 384-d float32). Deletes only flip a validity
        bit without freeing a block, and VACUUM cannot reclaim it, so churn
        bloats the file. A `collection PARTITION KEY` makes this worse: it
        reserves at least one full block per collection, and a NULL partition
        value gave every vector its own block (the original 374MB bug).

        This reads every live vector back out, drops and recreates chunks_vec
        with no partition key, and reinserts them into a single compact set of
        blocks. It doubles as the migration off the old partitioned schema.
        """
        rows = self._conn.execute(
            "SELECT rowid, embedding FROM chunks_vec ORDER BY rowid"
        ).fetchall()

        with self._conn:
            self._conn.execute("DROP TABLE chunks_vec")
            self._conn.execute(
                "CREATE VIRTUAL TABLE chunks_vec USING vec0("
                "embedding float[384] distance_metric=cosine)"
            )
            self._conn.executemany(
                "INSERT INTO chunks_vec(rowid, embedding) VALUES (?,?)",
                [(r["rowid"], r["embedding"]) for r in rows],
            )
        self._conn.execute("VACUUM")
        print(f"Rebuilt chunks_vec with {len(rows)} vectors.")

    def search(self, query: str, k:int=10):
        try:
            results = self.retrieval.rrf(query, k)
            return results
        except Exception as e:
            return [f"Error: {e}"]