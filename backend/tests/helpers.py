from tests.conftest import Client


def shifts_of(client: Client, day_id: str) -> dict:
    r = client.get(f"/api/registrations/{day_id}")
    assert r.status_code == 200, r.text
    return {s["kind"]: s["shift_id"] for s in r.json()["shifts"]}


def register(client: Client, day_id: str, **choices):
    """register(c, day, evening=True) or evening=("16:00") for a late arrival."""
    ids = shifts_of(client, day_id)
    body = []
    for kind, value in choices.items():
        if isinstance(value, str):
            body.append({"shift_id": ids[kind], "available": True, "arrival_time": value})
        else:
            body.append({"shift_id": ids[kind], "available": bool(value)})
    return client.put(f"/api/registrations/{day_id}", {"choices": body})


def board(mgr: Client, day_id: str) -> dict:
    r = mgr.post(f"/api/manage/days/{day_id}/draft")
    assert r.status_code == 200, r.text
    return r.json()


def shift(b: dict, kind: str) -> dict:
    return next(s for s in b["shifts"] if s["kind"] == kind)


def publish(mgr: Client, day_id: str):
    b = mgr.get(f"/api/manage/days/{day_id}/board").json()
    return mgr.post(f"/api/manage/days/{day_id}/publish",
                    {"draft_id": b["draft"]["id"], "expected_edit_count": b["draft"]["edit_count"]})


def assign(mgr: Client, slot_key: str, user_id: str):
    return mgr.post(f"/api/manage/slots/{slot_key}/assign", {"user_id": user_id})
