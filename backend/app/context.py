"""Per-request snapshot; explicitly copied into rendering and background threads."""
from __future__ import annotations

from collections.abc import MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass

from backend.app import storage


@dataclass(frozen=True)
class DatasetContext:
    owner_id: int
    session_hash: str
    dataset_id: str | None


dataset_context: ContextVar[DatasetContext | None] = ContextVar("dataset_context", default=None)


class ScopedJob(MutableMapping):
    """Keep existing progress callbacks while persisting each user's own jobs."""
    def __init__(self, kind: str):
        self.kind = kind
        self.fallback = {"status": "idle", "message": "", "started_at": None,
                         "finished_at": None, "progress": {}}

    def snapshot(self):
        context = dataset_context.get()
        return storage.load_job(context.owner_id, self.kind) if context else dict(self.fallback)

    def update(self, *args, **kwargs):
        state = self.snapshot()
        state.update(*args, **kwargs)
        context = dataset_context.get()
        if context:
            storage.save_job(context.owner_id, self.kind, state)
        else:
            self.fallback = state

    def __getitem__(self, key):
        return self.snapshot()[key]

    def __setitem__(self, key, value):
        self.update({key: value})

    def __delitem__(self, key):
        state = self.snapshot()
        del state[key]
        context = dataset_context.get()
        if context:
            storage.save_job(context.owner_id, self.kind, state)
        else:
            self.fallback = state

    def __iter__(self):
        return iter(self.snapshot())

    def __len__(self):
        return len(self.snapshot())
