import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

LOGGER = logging.getLogger("recommender")
DEFAULT_RECOMMENDER_MODEL = "avsolatorio/GIST-small-Embedding-v0"

try:
    import torch
except ImportError:
    torch = None

# Cache loaded encoders so a model is read from disk / torch is initialised only
# once per (model, device) for the lifetime of the process instead of on every
# rerank call. Guarded by a lock because workflow jobs run in daemon threads.
_ENCODER_CACHE: dict[tuple[str, str], SentenceTransformer] = {}
_ENCODER_CACHE_LOCK = threading.Lock()
_TORCH_THREADS_CONFIGURED = False


def _limit_torch_cpu_threads() -> None:
    """Cap the number of CPU threads torch uses so encoding does not saturate
    every core on a shared server. Controlled by EMBEDDING_CPU_THREADS
    (default: 4, or fewer if the machine has fewer cores). Set to 0 to leave
    torch's default untouched."""
    global _TORCH_THREADS_CONFIGURED
    if _TORCH_THREADS_CONFIGURED or torch is None:
        return
    _TORCH_THREADS_CONFIGURED = True
    raw = os.getenv("EMBEDDING_CPU_THREADS", "4").strip()
    try:
        requested = int(raw)
    except ValueError:
        requested = 4
    if requested <= 0:
        return
    available = os.cpu_count() or requested
    threads = max(1, min(requested, available))
    try:
        torch.set_num_threads(threads)
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("Failed to cap torch CPU threads: %s", exc)
    else:
        LOGGER.info("Capped torch CPU threads to %d", threads)


def _get_encoder(model: str, device: str, proxy: str | None) -> SentenceTransformer:
    """Return a cached SentenceTransformer for (model, device), loading it only
    on the first request. Subsequent rerank calls reuse the resident model."""
    key = (model, device)
    encoder = _ENCODER_CACHE.get(key)
    if encoder is not None:
        return encoder
    with _ENCODER_CACHE_LOCK:
        encoder = _ENCODER_CACHE.get(key)
        if encoder is not None:
            return encoder
        _limit_torch_cpu_threads()
        LOGGER.info(
            "Loading SentenceTransformer model=%s device=%s proxy=%s",
            model,
            device,
            "enabled" if proxy else "disabled",
        )
        with _temporary_proxy(proxy):
            encoder = SentenceTransformer(model, device=device)
        _ENCODER_CACHE[key] = encoder
        return encoder


@contextmanager
def _temporary_proxy(proxy: str | None):
    if not proxy:
        yield
        return

    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ[key] = proxy
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def resolve_embedding_device(device: str | None = None) -> str:
    requested = (device or os.getenv("EMBEDDING_DEVICE") or "auto").strip().lower()
    if requested == "auto":
        if torch is not None and torch.cuda.is_available():
            return "cuda"
        return "cpu"
    if requested.startswith("cuda"):
        if torch is None or not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested for embeddings, but PyTorch CUDA is not available.")
        return requested
    if requested == "cpu":
        return "cpu"
    return requested


def resolve_embedding_proxy(proxy: str | None = None) -> str | None:
    if proxy is not None:
        cleaned = proxy.strip()
        return cleaned or None
    for key in ("EMBEDDING_PROXY", "HF_PROXY", "HUGGINGFACE_PROXY"):
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return None


def rerank_paper(
    candidate: list[Any],
    corpus: list[dict],
    model: str = DEFAULT_RECOMMENDER_MODEL,
    device: str | None = None,
    proxy: str | None = None,
) -> list[Any]:
    resolved_device = resolve_embedding_device(device)
    resolved_proxy = resolve_embedding_proxy(proxy)
    encoder = _get_encoder(model, resolved_device, resolved_proxy)
    #sort corpus by date, from newest to oldest
    corpus = sorted(corpus,key=lambda x: datetime.strptime(x['data']['dateAdded'], '%Y-%m-%dT%H:%M:%SZ'),reverse=True)
    time_decay_weight = 1 / (1 + np.log10(np.arange(len(corpus)) + 1))
    time_decay_weight = time_decay_weight / time_decay_weight.sum()
    corpus_feature = encoder.encode([paper['data']['abstractNote'] for paper in corpus])
    candidate_feature = encoder.encode([paper.summary for paper in candidate])
    sim = encoder.similarity(candidate_feature,corpus_feature) # [n_candidate, n_corpus]
    scores = (sim * time_decay_weight).sum(axis=1) * 10 # [n_candidate]
    for s,c in zip(scores,candidate):
        c.score = s.item()
    candidate = sorted(candidate,key=lambda x: x.score,reverse=True)
    return candidate
