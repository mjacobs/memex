"""In-memory stand-in for google.cloud.firestore.Client.

Implements just the surface memex.store.firestore uses: collection ->
document(set/get/update) and collection -> where/order_by/start_after/
limit/stream. Used when FIRESTORE_EMULATOR_HOST is unset.
"""

from copy import deepcopy
from dataclasses import dataclass, field, replace


@dataclass
class FakeSnapshot:
    exists: bool
    _data: dict | None

    def to_dict(self) -> dict | None:
        return deepcopy(self._data) if self._data is not None else None


class FakeDocument:
    def __init__(self, collection: "FakeCollection", doc_id: str):
        self._collection = collection
        self._id = doc_id

    def set(self, data: dict) -> None:
        self._collection.docs[self._id] = deepcopy(data)

    def get(self) -> FakeSnapshot:
        data = self._collection.docs.get(self._id)
        return FakeSnapshot(exists=data is not None, _data=data)

    def update(self, changes: dict) -> None:
        if self._id not in self._collection.docs:
            raise KeyError(f"no document {self._id}")
        self._collection.docs[self._id].update(deepcopy(changes))

    def delete(self) -> None:
        self._collection.docs.pop(self._id, None)


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
