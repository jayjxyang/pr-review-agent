from dataclasses import dataclass, field
from functools import lru_cache

import tiktoken

from app.core.logging import get_logger
from app.services.github import FilePatch

logger = get_logger(__name__)

# cl100k_base is used by GPT-4 / GPT-3.5 and gives a close-enough approximation
# for other models (deepseek, etc.).  For exact counts, swap the encoding name.
_ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding(_ENCODING_NAME)


def count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


@dataclass
class DiffChunk:
    files: list[FilePatch] = field(default_factory=list)
    token_count: int = 0

    def add(self, fp: FilePatch, tokens: int) -> None:
        self.files.append(fp)
        self.token_count += tokens

    def format(self) -> str:
        """Render the chunk as a single diff string ready to embed in a prompt."""
        parts: list[str] = []
        for fp in self.files:
            parts.append(f"### {fp.filename}\n```diff\n{fp.patch}\n```")
        return "\n\n".join(parts)


def chunk_diff(patches: list[FilePatch], token_limit: int) -> list[DiffChunk]:
    """Pack FilePatch objects into DiffChunk buckets using a greedy first-fit strategy.

    A file whose patch alone exceeds *token_limit* is placed into its own chunk
    (we never split within a file at this stage).

    Args:
        patches:     Ordered list of file patches to pack.
        token_limit: Maximum tokens allowed per chunk.

    Returns:
        A non-empty list of DiffChunk objects, or an empty list if *patches* is empty.
    """
    if not patches:
        return []

    chunks: list[DiffChunk] = []
    current = DiffChunk()

    for fp in patches:
        tokens = count_tokens(fp.patch)

        if tokens > token_limit:
            # Oversized file: flush current chunk (if non-empty), emit solo chunk.
            if current.files:
                chunks.append(current)
                current = DiffChunk()
            solo = DiffChunk()
            solo.add(fp, tokens)
            chunks.append(solo)
            logger.warning(
                "oversized_file_chunk",
                filename=fp.filename,
                tokens=tokens,
                limit=token_limit,
            )
            continue

        if current.token_count + tokens > token_limit:
            # Current chunk is full — start a new one.
            chunks.append(current)
            current = DiffChunk()

        current.add(fp, tokens)

    if current.files:
        chunks.append(current)

    logger.info(
        "diff_chunked",
        files=len(patches),
        chunks=len(chunks),
        token_limit=token_limit,
        token_counts=[c.token_count for c in chunks],
    )
    return chunks
