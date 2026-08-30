"""In-memory stand-in for google.cloud.firestore.Client.

Implements just the surface memex.store.firestore uses: collection ->
document(set/get/update) and collection -> where/order_by/start_after/
limit/stream, plus write_option(last_update_time=...) preconditions (the
operations CAS). Used when FIRESTORE_EMULATOR_HOST is unset.
"""

from copy import deepcopy
from dataclasses import dataclass, field, replace

from google.api_core.exceptions import FailedPrecondition, NotFound
from google.cloud.firestore_v1.transforms import ArrayUnion


@dataclass
class FakeWriteOption:
    """What client.write_option(last_update_time=...) hands back."""

    last_update_time: object


@dataclass
class FakeSnapshot:
    exists: bool
    _data: dict | None
    update_time: object = None

    def to_dict(self) -> dict | None:
        return deepcopy(self._data) if self._data is not None else None


class FakeDocument:
    def __init__(self, collection: "FakeCollection", doc_id: str):
        self._collection = collection
        self._id = doc_id

    def set(self, data: dict) -> None:
        self._collection.docs[self._id] = deepcopy(data)
        self._collection.touch(self._id)

    def get(self) -> FakeSnapshot:
        data = self._collection.docs.get(self._id)
        return FakeSnapshot(
            exists=data is not None,
            _data=data,
            update_time=self._collection.times.get(self._id),
        )

    def update(self, changes: dict, option: FakeWriteOption | None = None) -> None:
        if self._id not in self._collection.docs:
            # What the real client raises, so callers can catch "gone"
            # without also swallowing a transient failure.
            raise NotFound(f"no document {self._id}")
        if option is not None and option.last_update_time != self._collection.times.get(
            self._id
        ):
            raise FailedPrecondition("document changed since it was read")
        doc = self._collection.docs[self._id]
        for key, value in deepcopy(changes).items():
            if isinstance(value, ArrayUnion):
                existing = list(doc.get(key) or [])
                # Firestore's ArrayUnion appends only values not already there.
                doc[key] = existing + [v for v in value.values if v not in existing]
            else:
                doc[key] = value
        self._collection.touch(self._id)

    def delete(self) -> None:
        self._collection.docs.pop(self._id, None)
        self._collection.times.pop(self._id, None)


def _matches(doc: dict, field_path: str, op: str, value: object) -> bool:
    if op == "==":
        return doc.get(field_path) == value
    if op == "array_contains":
        return value in (doc.get(field_path) or [])
    raise NotImplementedError(f"fake firestore: operator {op!r}")


@dataclass
class FakeQuery:
    collection: "FakeCollection"
    filters: list[tuple[str, str, object]] = field(default_factory=list)
    order_field: str | None = None
    descending: bool = False
    start_after_value: object = None
    max_results: int | None = None

    def where(self, filter) -> "FakeQuery":
        new = (filter.field_path, filter.op_string, filter.value)
        return replace(self, filters=[*self.filters, new])

    def order_by(self, field_path: str, direction: str = "ASCENDING") -> "FakeQuery":
        return replace(
            self, order_field=field_path, descending=(direction == "DESCENDING")
        )

    def start_after(self, doc: dict) -> "FakeQuery":
        assert self.order_field is not None
        return replace(self, start_after_value=doc[self.order_field])

    def limit(self, n: int) -> "FakeQuery":
        return replace(self, max_results=n)

    def stream(self):
        docs = [
            d
            for d in self.collection.docs.values()
            if all(_matches(d, *f) for f in self.filters)
        ]
        if self.order_field is not None:
            docs.sort(key=lambda d: d.get(self.order_field), reverse=self.descending)
            if self.start_after_value is not None:
                if self.descending:
                    docs = [
                        d for d in docs if d[self.order_field] < self.start_after_value
                    ]
                else:
                    docs = [
                        d for d in docs if d[self.order_field] > self.start_after_value
                    ]
        if self.max_results is not None:
            docs = docs[: self.max_results]
        yield from (FakeSnapshot(exists=True, _data=d) for d in docs)


class FakeCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        # Stand-in for Firestore's per-document update_time: a strictly
        # increasing counter is all a last_update_time precondition compares.
        self.times: dict[str, int] = {}
        self._clock = 0

    def touch(self, doc_id: str) -> None:
        self._clock += 1
        self.times[doc_id] = self._clock

    def document(self, doc_id: str) -> FakeDocument:
        return FakeDocument(self, doc_id)

    def where(self, filter) -> FakeQuery:
        return FakeQuery(self).where(filter)

    def order_by(self, field_path: str, direction: str = "ASCENDING") -> FakeQuery:
        return FakeQuery(self).order_by(field_path, direction)


class FakeFirestoreClient:
    def __init__(self):
        self._collections: dict[str, FakeCollection] = {}

    def collection(self, name: str) -> FakeCollection:
        return self._collections.setdefault(name, FakeCollection())

    def write_option(self, **kwargs) -> FakeWriteOption:
        return FakeWriteOption(last_update_time=kwargs.get("last_update_time"))
