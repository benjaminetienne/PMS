import sqlite3
import sqlite_vec
import re
from pydantic import BaseModel
from typing import Optional


class Hit(BaseModel):
    chunk_id: int
    score: float
    collection: str
    doc_title: str
    doc_path: str
    heading_path: str
    text: str
    bm25_rank: Optional[int] = None
    vec_rank: Optional[int] = None


class HybridRetrieval:
    def __init__(self, conn, embedding_model):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self.embedding_model = embedding_model

    def _bm25_search(self, query, k=10):
        query = " OR ".join(f'"{t}"' for t in re.findall(r"\w+", query))

        if not query: # Ie: query= "" or "???"
            return []
        
        with self._conn as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    rowid as id, 
                    heading_path, 
                    text, 
                    bm25(chunks_fts) as bm25_score, 
                    snippet(chunks_fts, 1, '<mark>', '</mark>', '...', 32) as snippet
                FROM chunks_fts 
                WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) 
                LIMIT ?""", (query, k))
            bm25_candidates = [row for row in cursor.fetchall()]
        return bm25_candidates
    
    def _vector_search(self, query, k=10):
        with self._conn as conn:
            cursor = conn.cursor()
            vector = list(self.embedding_model.query_embed(query))[0]
            cursor.execute(
                  """
                  SELECT 
                    knn.rowid as id, 
                    chunks.text, 
                    knn.distance as vec_score
                  FROM (
                      SELECT rowid, distance
                      FROM chunks_vec
                      WHERE embedding MATCH ? AND k = ?
                  ) AS knn
                  JOIN chunks ON chunks.id = knn.rowid
                  ORDER BY knn.distance
                  LIMIT ?
                  """,
                  (sqlite_vec.serialize_float32(vector), k, k),
              )
            vector_candidates = cursor.fetchall()
        return vector_candidates

    def candidates(self, query:str, k:int=10):
        
        bm25_ranks = {row['id']: rank for rank, row in enumerate(self._bm25_search(query, k), 1)}
        vec_ranks  = {row['id']: rank for rank, row in enumerate(self._vector_search(query, k), 1)}
        return (bm25_ranks, vec_ranks)


    def rrf(self, query: str, k:int=10):
        bm25_candidates, vector_candidates = self.candidates(query, k*10) # k*10 because otherwise we might filter edge candidates too early
        scores = dict()

        c = 1
        for rankings in [bm25_candidates, vector_candidates]:
            for chunk_id, rank in rankings.items():
                scores[chunk_id] = scores.get(chunk_id, 0) + 0.5 * (c + 1) / (c + rank)

        if not scores: # Not candidates
            return []
        
        marks = ",".join("?" * len(scores))
        with self._conn as conn:
            rows = conn.execute(
                f"""
                SELECT 
                c.id, 
                c.heading_path, 
                c.text,
                d.collection AS collection,
                d.title AS doc_title,
                d.path AS doc_path
                FROM chunks c
                JOIN documents d
                ON c.doc_id = d.id
                WHERE c.id IN ({marks})""", list(scores.keys())
            )
        
        
        return sorted([
            Hit(
                chunk_id=r['id'],
                text=r['text'], 
                score=scores.get(r['id']), 
                collection=r['collection'],
                doc_path=r['doc_path'],
                doc_title=r['doc_title'],
                heading_path=r['heading_path'],
                bm25_rank=bm25_candidates.get(r['id']), 
                vec_rank=vector_candidates.get(r['id'])
            ) for r in rows
        ], key= lambda x: x.score, reverse=True)[:k]