"""
Parse uploaded process materials into text chunks with source references.

Supported: .md .txt .vtt .srt .docx .pdf
Each chunk carries (file, location, text) so extraction can cite its sources.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class Chunk:
    file: str
    location: str       # timestamp, page number, or chunk index
    text: str


@dataclass
class ParsedFile:
    name: str
    kind: str           # transcript | document | unknown
    chunks: List[Chunk] = field(default_factory=list)
    chars: int = 0

    @property
    def summary(self) -> str:
        return f"{self.name}: {self.kind}, {len(self.chunks)} chunks, {self.chars:,} chars"


TIMESTAMP_RE = re.compile(r"^#{1,6}\s*(\d{1,2}:\d{2}:\d{2})\s*$", re.M)
VTT_TS_RE = re.compile(r"(\d{2}:\d{2}:\d{2})[.,]\d{3}\s*-->")


def _read_bytes(name: str, data: bytes) -> str:
    lower = name.lower()
    if lower.endswith(".docx"):
        import docx  # python-docx
        d = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs)
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(data))
        return "\n\f".join((p.extract_text() or "") for p in r.pages)
    return data.decode("utf-8", errors="replace")


def _chunk_by_timestamp(name: str, text: str) -> List[Chunk]:
    """Transcripts in markdown with '### HH:MM:SS' headers."""
    parts = TIMESTAMP_RE.split(text)
    # parts = [preamble, ts1, body1, ts2, body2, ...]
    chunks: List[Chunk] = []
    if parts[0].strip():
        chunks.append(Chunk(name, "preamble", parts[0].strip()))
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1].strip()
        if body:
            chunks.append(Chunk(name, parts[i], body))
    return chunks


def _chunk_vtt(name: str, text: str) -> List[Chunk]:
    chunks: List[Chunk] = []
    current_ts, buf = None, []
    for line in text.splitlines():
        m = VTT_TS_RE.search(line)
        if m:
            if buf and current_ts:
                chunks.append(Chunk(name, current_ts, " ".join(buf)))
            current_ts, buf = m.group(1), []
        elif line.strip() and not line.strip().isdigit() and line.strip() != "WEBVTT":
            buf.append(line.strip())
    if buf and current_ts:
        chunks.append(Chunk(name, current_ts, " ".join(buf)))
    # group every ~12 cues so chunks aren't tiny
    grouped: List[Chunk] = []
    for i in range(0, len(chunks), 12):
        g = chunks[i:i + 12]
        grouped.append(Chunk(name, g[0].location, " ".join(c.text for c in g)))
    return grouped


def _chunk_by_size(name: str, text: str, size: int = 2500) -> List[Chunk]:
    pages = text.split("\f")
    chunks: List[Chunk] = []
    if len(pages) > 1:
        for i, p in enumerate(pages, 1):
            if p.strip():
                chunks.append(Chunk(name, f"p.{i}", p.strip()))
        return chunks
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    buf, n = [], 0
    for p in paras:
        if n + len(p) > size and buf:
            chunks.append(Chunk(name, f"chunk {len(chunks) + 1}", "\n\n".join(buf)))
            buf, n = [], 0
        buf.append(p)
        n += len(p)
    if buf:
        chunks.append(Chunk(name, f"chunk {len(chunks) + 1}", "\n\n".join(buf)))
    return chunks


def parse_file(name: str, data: bytes) -> ParsedFile:
    text = _read_bytes(name, data)
    lower = name.lower()
    if lower.endswith((".vtt", ".srt")):
        chunks, kind = _chunk_vtt(name, text), "transcript"
    elif TIMESTAMP_RE.search(text):
        chunks, kind = _chunk_by_timestamp(name, text), "transcript"
    else:
        chunks, kind = _chunk_by_size(name, text), "document"
    return ParsedFile(name=name, kind=kind, chunks=chunks, chars=len(text))


def parse_path(path: str) -> ParsedFile:
    import os
    with open(path, "rb") as f:
        return parse_file(os.path.basename(path), f.read())


def to_prompt_text(files: List[ParsedFile], max_chars: int = 180_000) -> str:
    """Flatten parsed files into a single source block for the extraction prompt."""
    out, n = [], 0
    for pf in files:
        out.append(f"\n===== SOURCE FILE: {pf.name} ({pf.kind}) =====\n")
        for c in pf.chunks:
            block = f"[{pf.name} @ {c.location}]\n{c.text}\n"
            if n + len(block) > max_chars:
                out.append("[... truncated ...]\n")
                return "".join(out)
            out.append(block)
            n += len(block)
    return "".join(out)
