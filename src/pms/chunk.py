from pydantic import BaseModel
import re


class MarkdownChunk(BaseModel):
    seq: int
    heading_path: str       # e.g., "FlashAttention-3 > Benchmarks > FP8"
    text: str               # Raw markdown of the chunk
    embed_text: str         # Context-enriched text fed to the embedding model
    start_line: int         # 1-indexed inclusive
    end_line: int           # 1-indexed inclusive

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")

def chunk_markdown(
    markdown: str,
    title: str = "",
    target_chars: int = 1000,   # ~250-300 tokens per chunk (1 focused section)
    overlap_lines: int = 4,     # Context continuity across non-heading splits
) -> list[MarkdownChunk]:
    lines = markdown.splitlines()
    chunks: list[MarkdownChunk] = []

    heading_stack: dict[int, str] = {}  # level (1-6) -> heading text
    in_fence = False
    fence_marker = ""
    fence_chars = 0

    buf_lines: list[str] = []
    buf_start_line = 1
    buf_chars = 0
    chunk_heading_path = title

    def current_breadcrumb() -> str:
        parts = [heading_stack[k] for k in sorted(heading_stack.keys())]
        if title and (not parts or parts[0] != title):
            parts.insert(0, title)
        return " > ".join(parts)

    def flush_chunk(end_line_idx: int):
        nonlocal buf_lines, buf_chars
        text = "\n".join(buf_lines).strip()
        if not text:
            return
        prefix_parts = [p for p in (chunk_heading_path, ) if p]
        prefix = f"[{' | '.join(prefix_parts)}]\n" if prefix_parts else ""
        chunks.append(
            MarkdownChunk(
                seq=len(chunks),
                heading_path=chunk_heading_path,
                text=text,
                embed_text=f"{prefix}{text}",
                start_line=buf_start_line,
                end_line=end_line_idx,
            )
        )

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        # 1. Track code fences so we NEVER split inside a code block
        fence_match = FENCE_RE.match(stripped)
        if fence_match:
            marker = fence_match.group(1)[0]
            info_str = stripped[len(fence_match.group(1)):].strip()
            if not in_fence:
                in_fence = True
                fence_marker = marker
                fence_chars = 0
            elif marker == fence_marker and not info_str:
                in_fence = False
                fence_chars = 0
            elif info_str:
                # A fence with a language tag is always an opening fence (previous fence was stray)
                in_fence = True
                fence_marker = marker
                fence_chars = 0
        elif in_fence:
            fence_chars += len(line) + 1
            # Guard against a stray unclosed ``` from web extraction inverting in_fence forever
            if fence_chars > target_chars * 1.5:
                in_fence = False
                fence_chars = 0

        # 2. If not in a code block, check for Markdown headings
        if not in_fence:
            h_match = HEADING_RE.match(line)
            if h_match:
                level = len(h_match.group(1))
                _title = h_match.group(2).strip()

                # If buffer already has content, a new H1/H2/H3 is a natural split point
                if buf_chars >= (target_chars * 0.25) and level <= 3:
                    flush_chunk(idx - 1)
                    buf_lines = []
                    buf_chars = 0
                    buf_start_line = idx

                # Update heading hierarchy (clear deeper levels)
                heading_stack = {k: v for k, v in heading_stack.items() if k < level}
                heading_stack[level] = _title
                if not buf_lines:
                    chunk_heading_path = current_breadcrumb()

        # 3. Append line to buffer
        if not buf_lines:
            buf_start_line = idx
            chunk_heading_path = current_breadcrumb()
        buf_lines.append(line)
        buf_chars += len(line) + 1

        # 4. If we hit target_chars and we are OUTSIDE a code block, split on blank line
        if not in_fence and buf_chars >= target_chars:
            if line.strip() == "" or buf_chars >= target_chars * 1.4:
                flush_chunk(idx)
                # Keep a small line overlap ONLY if it doesn't slice into a code fence
                candidate_overlap = buf_lines[-overlap_lines:] if line.strip() != "" else []
                if any(FENCE_RE.match(l.strip()) for l in candidate_overlap):
                    overlap = []
                else:
                    overlap = list(candidate_overlap)
                buf_lines = overlap
                buf_chars = sum(len(x) + 1 for x in buf_lines)
                buf_start_line = idx - len(buf_lines) + 1
                chunk_heading_path = current_breadcrumb()

    # Flush remaining lines
    if buf_lines:
        flush_chunk(len(lines))

    return chunks