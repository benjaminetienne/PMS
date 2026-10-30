import trafilatura
from pydantic import BaseModel

class Extraction(BaseModel):
    url: str
    title: str | None
    content: str


def extract_from_url(url: str) -> Extraction:
    print(f"Fetching: {url}...")
    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        raise RuntimeError(f"Failed to fetch {url}")

    metadata = trafilatura.extract_metadata(downloaded)
    title = metadata.title if metadata else None

    content = trafilatura.extract(downloaded, output_format="markdown", include_links=True)
    if not content:
        raise RuntimeError(f"Failed to extract content from {url}")

    return Extraction(url=url, title=title, content=content)
