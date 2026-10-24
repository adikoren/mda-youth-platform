"""Web Push subscriptions and the self-test notification (decision of 2026-10-17).

The recipient is always the authenticated account. A client can never name another user's device: an endpoint
that belongs to someone else is treated as not found. Endpoints and keys are never echoed back or logged."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.access.dependencies import get_active_actor, now_dep
from app.audit.service import record
from app.core.db import get_db
from app.core.errors import AppError, bad_request, conflict, not_found
from app.notifications import push
from app.notifications.models import PushSubscription

router = APIRouter(prefix="/api/push", tags=["push"])

# The one generic message this stage sends. No schedule or personal content.
TEST_PAYLOAD = {"title": "מד״א", "body": "ההתראות שלך פועלות ❤️", "url": "/notifications", "tag": "self-test"}

# Per-process rate limit for the test action (the app runs with one worker; see D-52).
_last_test_at: dict[uuid.UUID, datetime] = {}


class KeysIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=64)


class SubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1, max_length=1024)
    keys: KeysIn


class EndpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1, max_length=1024)


def _mine(db: Session, actor: Actor, endpoint: str) -> PushSubscription | None:
    return db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint,
                                                    PushSubscription.user_id == actor.user_id))


@router.get("/config")
def config():
    """The public VAPID key the browser needs to subscribe. The private key never leaves the server."""
    return {"public_key": push.public_key(), "configured": push.configured()}


@router.post("/subscriptions")
def subscribe(body: SubscriptionIn, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
              now: datetime = Depends(now_dep)):
    if not push.valid_endpoint(body.endpoint):
        raise bad_request("bad_endpoint", "כתובת ההתראות אינה נתמכת")
    if not push.valid_keys(body.keys.p256dh, body.keys.auth):
        raise bad_request("bad_keys", "מפתחות ההתראות אינם תקינים")
    row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint).with_for_update())
    previous_owner = None
    if row is None:
        row = PushSubscription(user_id=actor.user_id, endpoint=body.endpoint, p256dh=body.keys.p256dh,
                               auth=body.keys.auth, created_at=now, last_seen_at=now)
        db.add(row)
    else:
        # Same device: re-assigned to the account that is signed in now (account switch on this device).
        previous_owner = row.user_id
        row.user_id, row.p256dh, row.auth, row.last_seen_at = actor.user_id, body.keys.p256dh, body.keys.auth, now
    record(db, actor_id=actor.user_id, action="push_subscribe", object_type="user", object_id=actor.user_id, now=now,
           meta={"reassigned": previous_owner is not None and previous_owner != actor.user_id})
    db.commit()
    return {"registered": True}


@router.post("/subscriptions/check")
def check(body: EndpointIn, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db)):
    """Is this device registered to the signed-in account? Reveals nothing about other accounts."""
    return {"registered_to_me": _mine(db, actor, body.endpoint) is not None}


@router.post("/subscriptions/remove")
def remove(body: EndpointIn, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
           now: datetime = Depends(now_dep)):
    """Detach this device from the account (turning notifications off here, or signing out)."""
    removed = db.execute(delete(PushSubscription).where(PushSubscription.endpoint == body.endpoint,
                                                        PushSubscription.user_id == actor.user_id)).rowcount
    if removed:
        record(db, actor_id=actor.user_id, action="push_unsubscribe", object_type="user", object_id=actor.user_id,
               now=now)
    db.commit()
    return {"removed": bool(removed)}


@router.post("/test")
def self_test(body: EndpointIn, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
              now: datetime = Depends(now_dep)):
    """Sends the generic test message to THIS account's device only, rate-limited. The result is what the push
    service answered; it is not proof that the message was displayed."""
    if not push.configured():
        raise AppError(503, "push_not_configured", "שירות ההתראות אינו מוגדר בשרת")
    last = _last_test_at.get(actor.user_id)
    interval = push.get_settings().push_test_interval_seconds
    if last is not None and (now - last).total_seconds() < interval:
        raise AppError(429, "test_rate_limited", "אפשר לשלוח התראת בדיקה פעם בכמה שניות. נסו שוב מאוחר יותר")
    row = _mine(db, actor, body.endpoint)
    if row is None:
        raise not_found("subscription_not_found", "ההתראות לא פעילות במכשיר הזה")
    _last_test_at[actor.user_id] = now
    result = push.send(row.endpoint, row.p256dh, row.auth, TEST_PAYLOAD)
    if result is push.PushResult.GONE:
        db.delete(row)
        db.commit()
        return {"result": "subscription_expired", "delivery_confirmed": False}
    if result is push.PushResult.NOT_CONFIGURED:
        raise AppError(503, "push_not_configured", "שירות ההתראות אינו מוגדר בשרת")
    messages = {
        push.PushResult.ACCEPTED: "accepted_by_push_service",
        push.PushResult.TRANSIENT: "temporarily_unavailable",
        push.PushResult.REJECTED: "rejected_by_push_service",
    }
    return {"result": messages[result], "delivery_confirmed": False}
