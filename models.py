"""
Foundation Model API client.

Wraps the Databricks-hosted, OpenAI-compatible serving endpoints for:
  * embeddings  -> databricks-gte-large-en (1024-dim)
  * chat        -> databricks-meta-llama-3-3-70b-instruct (tool-calling capable)

Auth is inherited from the WorkspaceClient, so the same code works in notebooks,
jobs, and the deployed Databricks App with no tokens in code.

Reference (Databricks docs, "Query an embedding model" / "Query a chat model"):
    w = WorkspaceClient()
    client = w.serving_endpoints.get_open_ai_client()
    client.embeddings.create(model="databricks-gte-large-en", input=...)
    client.chat.completions.create(model="databricks-meta-llama-3-3-70b-instruct", messages=...)
"""

import functools
import logging

from databricks.sdk import WorkspaceClient

import config

logger = logging.getLogger("trip-planner.models")

# GTE-large-en accepts batched input; keep batches modest to stay well within limits.
_EMBED_BATCH = 100


@functools.lru_cache(maxsize=1)
def _client():
    """OpenAI-compatible client bound to this workspace's serving endpoints."""
    return WorkspaceClient().serving_endpoints.get_open_ai_client()


def embed_texts(texts: list[str], batch_size: int = _EMBED_BATCH) -> list[list[float]]:
    """Embed a list of strings; returns one 1024-dim vector per input, in order."""
    if not texts:
        return []
    client = _client()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        resp = client.embeddings.create(model=config.EMBEDDING_ENDPOINT, input=chunk)
        # resp.data is returned in the same order as the input list.
        vectors.extend(item.embedding for item in resp.data)
    return vectors


def embed_text(text: str) -> list[float]:
    """Embed a single string -> one 1024-dim vector."""
    return embed_texts([text])[0]


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
):
    """Chat completion. Pass `tools` (OpenAI tool schema) to enable tool-calling.

    Returns the raw OpenAI-style response; callers read
    response.choices[0].message (.content and/or .tool_calls).
    """
    client = _client()
    kwargs: dict = {
        "model": config.CHAT_ENDPOINT,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice or "auto"
    return client.chat.completions.create(**kwargs)


if __name__ == "__main__":
    # Smoke test — run inside Databricks (uses workspace auth).
    logging.basicConfig(level=logging.INFO)
    v = embed_text("A weather-aware itinerary for a mountain hiking trip.")
    print(f"embedding dim = {len(v)} (expected {config.EMBEDDING_DIM})")
    r = chat([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=5)
    print("chat says:", r.choices[0].message.content)
