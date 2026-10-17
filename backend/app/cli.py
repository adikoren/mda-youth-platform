"""Private setup commands (run on the server, never exposed over HTTP).

  python -m app.cli init                         seed station + 7 registration templates
  python -m app.cli create-admin --email E --name N   first admin; password prompted (or MDA_ADMIN_PASSWORD)
  python -m app.cli generate-days --start D --end D
  python -m app.cli seed-demo                    synthetic personas (prints the generated demo password once)
"""
import argparse
import getpass
import os
import secrets
import sys
from datetime import date, timedelta

from sqlalchemy import select

from app.access.clock import get_clock
from app.accounts import service as accounts
from app.accounts.models import User
from app.accounts.security import MIN_PASSWORD_LENGTH, hash_password, normalize_email
from app.audit.service import record
from app.core.db import session_factory
from app.core.timeutil import local_today
from app.days import service as days


def cmd_init(_args) -> None:
    now = get_clock().now()
    with session_factory()() as db:
        days.get_station(db)
        created = days.ensure_templates(db, now)
        db.commit()
    print(f"station ready; templates created: {created}")


def _read_password() -> str:
    pw = os.environ.get("MDA_ADMIN_PASSWORD")
    if not pw:
        pw = getpass.getpass("Admin password: ")
        if pw != getpass.getpass("Repeat: "):
            sys.exit("passwords differ")
    if len(pw) < MIN_PASSWORD_LENGTH:
        sys.exit(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return pw


def cmd_create_admin(args) -> None:
    now = get_clock().now()
    email = normalize_email(args.email)
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, full_name=args.name, password_hash=hash_password(_read_password()),
                        status="active", created_at=now, approved_at=now)
            db.add(user)
            db.flush()
        else:
            user.status = "active"
        accounts.set_authority_role(db, user.id, "admin", True, None, now)
        record(db, actor_id=None, action="admin_bootstrap", object_type="user", object_id=user.id, now=now)
        db.commit()
    print(f"admin ready: {email}")


def cmd_generate_days(args) -> None:
    now = get_clock().now()
    with session_factory()() as db:
        n = days.generate_days(db, date.fromisoformat(args.start), date.fromisoformat(args.end), now)
        db.commit()
    print(f"created {n} activity days")


# Synthetic personas only. Names are invented; emails use the reserved .test domain.
WEEKDAY_SLUGS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
FIRST = ["נועה", "איתי", "מאיה", "יואב", "תמר", "עומר", "שירה", "אורי", "ליה", "דניאל", "הילה", "רועי",
         "אביגיל", "גיא", "יעל", "נדב", "רוני", "אלון", "מיכל", "עידו", "שחר", "טל", "אדם", "ענבר"]
LAST = ["כהן", "לוי", "מזרחי", "פרץ", "ביטון", "אברהם", "פרידמן", "שפירא", "דהן", "אזולאי", "חדד", "גבאי"]


def cmd_seed_demo(args) -> None:
    now = get_clock().now()
    password = os.environ.get("MDA_DEMO_PASSWORD") or secrets.token_urlsafe(9)
    pw_hash = hash_password(password)
    i = 0

    def person(db, email, level, home, extra_roles=()):
        nonlocal i
        name = f"{FIRST[i % len(FIRST)]} {LAST[(i + i // len(FIRST)) % len(LAST)]}"
        i += 1
        if db.scalar(select(User.id).where(User.email == email)):
            return db.scalar(select(User).where(User.email == email))
        u = User(email=email, full_name=name, phone=f"050-000{i:04d}", password_hash=pw_hash, status="active",
                 created_at=now, approved_at=now)
        db.add(u)
        db.flush()
        accounts.set_level(db, u.id, level, None, now)
        accounts.set_home_weekday(db, u.id, home, None, now)
        for r in extra_roles:
            accounts.set_authority_role(db, u.id, r, True, None, now)
        return u

    with session_factory()() as db:
        days.get_station(db)
        days.ensure_templates(db, now)
        person(db, "admin@example.test", "mentor", None, ("admin",))
        person(db, "youth.manager@example.test", "mentor", None, ("youth_manager",))
        for wd, slug in enumerate(WEEKDAY_SLUGS[:6]):  # Sun-Fri home days; Saturday is open to all
            mgr = person(db, f"{slug}.manager@example.test", "mentor", wd)
            db.flush()
            accounts.set_day_managers(db, wd, [mgr.id], None, now)
            person(db, f"{slug}.mentor@example.test", "mentor", wd)
            person(db, f"{slug}.youth@example.test", "youth", wd)
            for k in range(1, 4):
                person(db, f"{slug}.trainee{k}@example.test", "trainee", wd)
        sat_mgr = db.scalar(select(User).where(User.email == "fri.mentor@example.test"))
        accounts.set_day_managers(db, 6, [sat_mgr.id], None, now)
        if not db.scalar(select(User.id).where(User.email == "pending@example.test")):
            db.add(User(email="pending@example.test", full_name="משתמש ממתין", password_hash=pw_hash,
                        status="pending", requested_weekday=4, created_at=now))
        today = local_today(now)
        # Demo data shows full days (every shift type the day kind allows); real dates default to evening only.
        days.generate_days(db, today - timedelta(days=3), today + timedelta(days=args.days), now, shift_kinds=None)
        db.commit()
    print("demo personas ready (synthetic data only)")
    print("emails: admin@example.test, youth.manager@example.test, <day>.manager|mentor|youth|trainee1..3@example.test")
    print("        day = sun mon tue wed thu fri; Saturday managed by fri.mentor; pending@example.test")
    print(f"demo password (shown once, not stored anywhere): {password}")


def cmd_report_capacity(_args) -> None:
    """Read-only: list ambulance slots whose capacity is above the current limit (3)."""
    from app.scheduling.service import MAX_CAPACITY, slots_over_capacity_limit
    with session_factory()() as db:
        rows = slots_over_capacity_limit(db)
    if not rows:
        print(f"no ambulance slots above capacity {MAX_CAPACITY}")
        return
    print(f"{len(rows)} ambulance slot(s) above capacity {MAX_CAPACITY} (not changed; review manually):")
    for r in rows:
        print(f"  {r['date']} {r['shift']:<13} ambulance {r['ambulance']}  capacity {r['capacity']}  "
              f"version {r['revision']} ({r['version_status']})")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    a = sub.add_parser("create-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--name", required=True)
    a.set_defaults(fn=cmd_create_admin)
    g = sub.add_parser("generate-days")
    g.add_argument("--start", required=True)
    g.add_argument("--end", required=True)
    g.set_defaults(fn=cmd_generate_days)
    s = sub.add_parser("seed-demo")
    s.add_argument("--days", type=int, default=21)
    s.set_defaults(fn=cmd_seed_demo)
    sub.add_parser("report-capacity").set_defaults(fn=cmd_report_capacity)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
