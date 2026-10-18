from datetime import datetime, timezone

from aggregator.sources import crowdmark
from aggregator.store import Store

# Trimmed from the real /api/v2/student/assignments?include[]=exam-master response shape.
ASSIGNMENTS = {
    "data": [
        {"id": "cb3f-w3", "type": "assignments",
         "attributes": {"due": "2026-09-30T20:05:59.000Z", "submitted-at": None, "marks-sent-at": None,
                        "penalty-value": 1, "penalty-period": "minute"},
         "relationships": {"exam-master": {"data": {"id": "w3-13afe", "type": "exam-masters"}}}},
        {"id": "c132-w2", "type": "assignments",
         "attributes": {"due": "2026-09-23T20:05:59.000Z", "submitted-at": "2026-09-23T15:40:45.673Z",
                        "marks-sent-at": None},
         "relationships": {"exam-master": {"data": {"id": "w2-42c4b", "type": "exam-masters"}}}},
        {"id": "ef86-rich", "type": "assignments",
         "attributes": {"due": "2026-09-29T03:59:59.000Z", "submitted-at": None},
         "relationships": {"exam-master": {"data": {"id": "rich-solution-mp-problem-module-1-75e90"}}}},
    ],
    "included": [
        {"id": "w3-13afe", "type": "exam-masters", "attributes": {"title": "W3", "total-points": "25.0"},
         "relationships": {"course": {"data": {"id": "math-135-fall-2026"}}}},
        {"id": "w2-42c4b", "type": "exam-masters", "attributes": {"title": "W2", "total-points": "30.0"},
         "relationships": {"course": {"data": {"id": "math-135-fall-2026"}}}},
        {"id": "rich-solution-mp-problem-module-1-75e90", "type": "exam-masters",
         "attributes": {"title": "Rich Solution MP Problem - Module 1", "total-points": "4.0"},
         "relationships": {"course": {"data": {"id": "phys-121-fall-2026"}}}},
    ],
}
COURSES = {"data": [{"id": "math-135-fall-2026", "attributes": {"name": "MATH 135 - Fall 2026"}},
                    {"id": "phys-121-fall-2026", "attributes": {"name": "PHYS 121 - Fall 2026"}}]}


def test_parse():
    items = {i.title: i for i in crowdmark.parse(ASSIGNMENTS, COURSES)}
    w3 = items["W3"]
    assert (w3.source, w3.type, w3.context) == ("crowdmark", "assignment", "MATH 135")
    assert w3.due_date == datetime(2026, 9, 30, 20, 5, 59, tzinfo=timezone.utc)
    assert w3.source_link == "https://app.crowdmark.com/student/assessments/w3-13afe"
    assert w3.extra == {"points": "25.0", "late_penalty": "1 per minute"}
    assert items["Rich Solution MP Problem - Module 1"].context == "PHYS 121"
    assert items["W2"].extra["completed_at"] == "2026-09-23T15:40:45.673Z"


def test_submitted_items_become_done_once():
    store = Store(":memory:")
    items = crowdmark.parse(ASSIGNMENTS, COURSES)
    store.sync_source("crowdmark", items)
    status = {i.title: i.status for i in store.list_items(include_past=True)}
    assert status == {"W3": "not_started", "W2": "done", "Rich Solution MP Problem - Module 1": "not_started"}

    # The user reopens W2 (say, to fix something) — the next sync must not force it back to done.
    w2 = next(i for i in items if i.title == "W2")
    store.set_status(w2.id, "in_progress")
    store.sync_source("crowdmark", items)
    assert store.get_item(w2.id).status == "in_progress"


def test_unconfigured_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(crowdmark, "CONFIGURED_MARKER", tmp_path / "nope")
    assert not crowdmark.is_configured()
