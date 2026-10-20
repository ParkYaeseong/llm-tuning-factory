"""Local draft projects and transactional, content-addressed definition revisions."""

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import Field, JsonValue, field_validator, model_validator

from .config import StrictModel
from .registry import canonical, digest

LocalId = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-][a-zA-Z0-9_.-]*$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
T = TypeVar("T", bound=StrictModel)


class Revision(StrictModel, Generic[T]):
    spec: T
    revision_sha256: Sha256


class DatasetRef(StrictModel):
    dataset_id: LocalId
    version: LocalId
    sha256: Sha256


class DatasetBinding(StrictModel):
    binding_id: LocalId
    role: Literal["train", "validation", "test"]
    dataset: DatasetRef


def _json_object(value):
    """Reject Python-only values instead of silently coercing draft graph data."""
    if type(value) is not dict:
        raise ValueError("graph must be a JSON object")

    def check(item):
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("graph JSON object keys must be strings")
            for child in item.values():
                check(child)
        elif type(item) is list:
            for child in item:
                check(child)
        elif item is not None and type(item) not in {str, int, float, bool}:
            raise ValueError("graph must contain only JSON values")

    try:
        check(value)
        return json.loads(canonical(value))
    except (RecursionError, TypeError) as exc:
        raise ValueError("graph must contain finite, acyclic JSON values") from exc


class ProjectSpec(StrictModel):
    schema_version: Literal[1] = 1
    project_id: LocalId
    name: str = Field(min_length=1, max_length=200)
    purpose: str = ""
    task_type: str = ""
    datasets: tuple[DatasetBinding, ...] = ()
    default_executor_profile_id: LocalId | None = None
    graph: dict[str, JsonValue] = Field(default_factory=dict)
    legacy_origin: LocalId | None = None

    _graph_json = field_validator("graph", mode="before")(_json_object)

    @model_validator(mode="after")
    def unique_bindings(self):
        if len({binding.binding_id for binding in self.datasets}) != len(self.datasets):
            raise ValueError("duplicate dataset binding ID")
        return self


class _RevisionStore(Generic[T]):
    """SQLite is the single repository lock for revision CAS and pointer writes.

    Bodies and their pointers commit together, so a failed/stale save cannot expose
    a partial revision. Each read verifies the immutable body's content hash.
    """

    def __init__(self, root, namespace, spec_type, id_field):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "workflow-revisions.sqlite3"
        self.namespace, self.spec_type, self.id_field = namespace, spec_type, id_field
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS definition_revisions (
                    namespace TEXT NOT NULL, entity_id TEXT NOT NULL,
                    revision_sha256 TEXT NOT NULL, body BLOB NOT NULL,
                    PRIMARY KEY (namespace, entity_id, revision_sha256));
                CREATE TABLE IF NOT EXISTS definition_current (
                    namespace TEXT NOT NULL, entity_id TEXT NOT NULL, revision_sha256 TEXT NOT NULL,
                    PRIMARY KEY (namespace, entity_id));
                CREATE TABLE IF NOT EXISTS project_creation_requests (
                    request_id TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL,
                    project_id TEXT NOT NULL, revision_sha256 TEXT NOT NULL);
            """)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _checked_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]*", value):
            raise ValueError("invalid definition identifier")
        return value

    @staticmethod
    def _checked_hash(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("invalid revision hash")
        return value

    def _get(self, db, entity_id, revision=None):
        entity_id = self._checked_id(entity_id)
        if revision is None:
            current = db.execute(
                "SELECT revision_sha256 FROM definition_current WHERE namespace=? AND entity_id=?",
                (self.namespace, entity_id),
            ).fetchone()
            if current is None:
                raise ValueError("unknown definition")
            revision = current[0]
        revision = self._checked_hash(revision)
        row = db.execute(
            "SELECT body FROM definition_revisions WHERE namespace=? AND entity_id=? AND revision_sha256=?",
            (self.namespace, entity_id, revision),
        ).fetchone()
        if row is None:
            raise ValueError("unknown definition revision")
        if digest(row[0]) != revision:
            raise ValueError("definition revision hash mismatch")
        spec = self.spec_type.model_validate_json(row[0])
        if getattr(spec, self.id_field) != entity_id:
            raise ValueError("definition revision identity mismatch")
        return Revision[self.spec_type](spec=spec, revision_sha256=revision)

    def _save(self, db, spec, expected_revision):
        # model_copy(update=...) bypasses Pydantic validation; never trust it here.
        spec = self.spec_type.model_validate(spec.model_dump())
        entity_id = getattr(spec, self.id_field)
        if expected_revision is not None:
            self._checked_hash(expected_revision)
        current = db.execute(
            "SELECT revision_sha256 FROM definition_current WHERE namespace=? AND entity_id=?",
            (self.namespace, entity_id),
        ).fetchone()
        actual = current[0] if current else None
        if actual != expected_revision:
            raise ValueError("definition revision conflict")
        body = canonical(spec.model_dump(mode="json"))
        revision = digest(body)
        existing = db.execute(
            "SELECT body FROM definition_revisions WHERE namespace=? AND entity_id=? AND revision_sha256=?",
            (self.namespace, entity_id, revision),
        ).fetchone()
        if existing and existing[0] != body:
            raise ValueError("immutable definition revision hash mismatch")
        db.execute("INSERT OR IGNORE INTO definition_revisions VALUES (?,?,?,?)",
                   (self.namespace, entity_id, revision, body))
        db.execute("INSERT INTO definition_current VALUES (?,?,?) "
                   "ON CONFLICT(namespace,entity_id) DO UPDATE SET revision_sha256=excluded.revision_sha256",
                   (self.namespace, entity_id, revision))
        return self._get(db, entity_id, revision)

    def save(self, spec: T, expected_revision: str | None = None) -> Revision[T]:
        """Create only if absent (None), or replace the matching current revision."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._save(db, spec, expected_revision)

    def get(self, id: str, revision: str | None = None) -> Revision[T]:
        with self._connection() as db:
            return self._get(db, id, revision)

    def get_by_revision(self, revision_sha256: str) -> Revision[T]:
        """The revision a graph pins by hash alone (a train node's ``recipe_revision``).

        Exactly one entity of this namespace must hold it ("unknown definition revision" otherwise); the body is
        verified as in ``get``. Bodies include the entity ID, so two entities never share a revision hash.
        """
        revision = self._checked_hash(revision_sha256)
        with self._connection() as db:
            rows = db.execute("SELECT entity_id FROM definition_revisions WHERE namespace=? AND revision_sha256=?",
                              (self.namespace, revision)).fetchall()
            if len(rows) != 1:
                raise ValueError("unknown definition revision")
            return self._get(db, rows[0][0], revision)

    def list(self) -> list[Revision[T]]:
        with self._connection() as db:
            # One read snapshot keeps pointers and immutable bodies consistent.
            db.execute("BEGIN")
            ids = db.execute("SELECT entity_id FROM definition_current WHERE namespace=? ORDER BY entity_id",
                             (self.namespace,)).fetchall()
            return [self._get(db, row[0]) for row in ids]


class ProjectStore(_RevisionStore[ProjectSpec]):
    def __init__(self, root):
        super().__init__(root, "projects", ProjectSpec, "project_id")

    def create(self, request_id: str, name: str, purpose: str = "", task_type: str = "") -> Revision[ProjectSpec]:
        """Retrying an identical request returns its original creation revision."""
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request ID must be nonempty")
        spec = ProjectSpec(project_id=uuid.uuid4().hex, name=name, purpose=purpose, task_type=task_type)
        request_hash = digest(canonical({"name": name, "purpose": purpose, "task_type": task_type}))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT request_sha256,project_id,revision_sha256 FROM project_creation_requests "
                                  "WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing[0] != request_hash:
                    raise ValueError("request ID reused with different project details")
                return self._get(db, existing[1], existing[2])
            created = self._save(db, spec, None)
            db.execute("INSERT INTO project_creation_requests VALUES (?,?,?,?)",
                       (request_id, request_hash, spec.project_id, created.revision_sha256))
            return created
