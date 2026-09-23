"""Integration tests for the home membership API (P0.8).

Covers the four routes added in ``app/api/v1/homes.py``:

- ``GET /homes/{id}/members``            — list every member of a home.
- ``POST /homes/{id}/members``           — invite an existing user by email.
- ``PATCH /homes/{id}/members/{user_id}`` — change a member's role.
- ``DELETE /homes/{id}/members/{user_id}`` — remove a member.

Auth invariants baked into these tests (per project convention):

- No ``Authorization`` header → 401 (``get_actor`` rejects).
- A caller who is not a member of the home in the path → 404, never 403
  (so the API never leaks the existence of another home's roster).
- A caller who IS a member but NOT an owner, calling a management route
  → 403 (the one place 403 is legitimate inside a home).
- Unknown email on invite → 404 not_found「该邮箱还没注册账号」.
- Already a member on invite → 409 conflict (unique-index hit).
- Last-owner guard on demote / remove → 409 conflict.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.enums import HomeRole
from app.models import Home, HomeMembership
from app.models import User as UserModel
from tests.conftest import SeededActor

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------- helpers


async def _make_user(
    db_engine,
    *,
    email: str | None = None,
    display_name: str = "Friend",
) -> UserModel:
    """Insert a second ``User`` row that can be invited by email."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    email = email or f"friend-{uuid.uuid4().hex}@example.com"
    async with factory() as session:
        user = UserModel(
            id=user_id,
            email=email,
            password_hash="x",
            display_name=display_name,
        )
        session.add(user)
        await session.commit()
    return user


async def _add_member(
    db_engine, *, home_id: uuid.UUID, user_id: uuid.UUID, role: HomeRole
) -> None:
    """Insert a membership row directly (bypass the API)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            HomeMembership(
                home_id=home_id, user_id=user_id, role=role.value
            )
        )
        await session.commit()


async def _new_home(db_engine, *, name: str = "Other Home") -> UserModel:
    """Build a second user + home + membership, return the user.

    Used to set up the cross-home / cross-user scenarios below — those tests
    need a *second* seeded actor that has their own home.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    home_id = uuid.uuid4()
    email = f"other-{uuid.uuid4().hex}@example.com"
    async with factory() as session:
        other = UserModel(
            id=user_id,
            email=email,
            password_hash="x",
            display_name="Other",
        )
        session.add(other)
        await session.flush()
        session.add(
            Home(
                id=home_id,
                name=name,
                owner_id=user_id,
            )
        )
        await session.flush()
        session.add(
            HomeMembership(
                home_id=home_id,
                user_id=user_id,
                role=HomeRole.OWNER.value,
            )
        )
        await session.commit()
    return other


# --------------------------------------------------------------- list


async def test_list_members_includes_self(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """The roster carries the caller (as owner) and nothing else on a fresh house."""
    resp = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    me = body[0]
    assert me["user_id"] == str(seeded_actor.user_id)
    assert me["role"] == "owner"
    assert me["email"]
    assert me["joined_at"]


async def test_list_members_non_member_is_404(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """A caller outside this home gets the same 404 as for a non-existent home."""
    # The seeded actor names a random UUID as ``X-Home-Id`` — ``get_actor``
    # itself rejects with 404 because they are not a member of that home.
    resp = api_client.get(
        f"/api/v1/homes/{uuid.uuid4()}/members",
        headers={
            **seeded_actor.headers(),
            "X-Home-Id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 404


async def test_list_members_no_credentials_is_401(
    api_client: TestClient, seeded_actor
) -> None:
    """``Authorization`` missing but ``X-Home-Id`` present → 401.

    The bare inverse (no headers at all) is 422 because ``X-Home-Id`` is
    required by FastAPI's header validator; we want the *credential* check
    to fire, so we supply the selector and omit the bearer.
    """
    resp = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers={"X-Home-Id": str(seeded_actor.home_id)},
    )
    assert resp.status_code == 401


# --------------------------------------------------------------- invite


async def test_invite_member_happy_path(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """Adding an existing user by email returns 200 + the new member view."""
    friend = await _make_user(db_engine, display_name="Alice")

    resp = api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=seeded_actor.headers(),
        json={"email": friend.email, "role": "member"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == str(friend.id)
    assert body["display_name"] == "Alice"
    assert body["email"] == friend.email
    assert body["role"] == "member"

    # A subsequent list shows both rows.
    list_resp = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=seeded_actor.headers(),
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 2


async def test_invite_unknown_email_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    """An email with no matching ``User`` → 404 with the documented message."""
    resp = api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=seeded_actor.headers(),
        json={"email": "nobody@example.com", "role": "member"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"
    assert "还没注册账号" in body["error"]["message"]


async def test_invite_already_member_is_409(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """Re-inviting an existing member → 409 conflict."""
    friend = await _make_user(db_engine)
    # First invite: 200.
    first = api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=seeded_actor.headers(),
        json={"email": friend.email, "role": "member"},
    )
    assert first.status_code == 200, first.text

    # Second invite: 409.
    second = api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=seeded_actor.headers(),
        json={"email": friend.email, "role": "member"},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


async def test_invite_as_non_owner_is_403(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """A member-but-not-owner calling POST → 403 (the one place 403 is OK)."""
    # Set up a second user + their own home, then *also* add them as a MEMBER
    # to the seeded actor's home.
    other = await _new_home(db_engine)
    await _add_member(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=other.id,
        role=HomeRole.MEMBER,
    )

    # `other`'s token, naming the seeded actor's home in ``X-Home-Id``.
    from app.services.security import create_access_token

    headers = {
        "Authorization": f"Bearer {create_access_token(other.id)}",
        "X-Home-Id": str(seeded_actor.home_id),
    }
    resp = api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=headers,
        json={"email": "nobody@example.com", "role": "member"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_invite_validation_error_is_422(
    api_client: TestClient, seeded_actor
) -> None:
    """A role outside the enum + extra fields → 422 (Pydantic validation)."""
    resp = api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=seeded_actor.headers(),
        json={"email": "x@example.com", "role": "admin", "extra": 1},
    )
    assert resp.status_code == 422


# --------------------------------------------------------------- change role


async def test_change_role_happy_path(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """Promote a member to owner (two owners) → 200."""
    friend = await _make_user(db_engine)
    api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=seeded_actor.headers(),
        json={"email": friend.email, "role": "member"},
    )

    resp = api_client.patch(
        f"/api/v1/homes/{seeded_actor.home_id}/members/{friend.id}",
        headers=seeded_actor.headers(),
        json={"role": "owner"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "owner"


async def test_demote_last_owner_is_409(
    api_client: TestClient, seeded_actor
) -> None:
    """Demoting the only owner → 409 (cannot strand the home with no admin)."""
    resp = api_client.patch(
        f"/api/v1/homes/{seeded_actor.home_id}/members/{seeded_actor.user_id}",
        headers=seeded_actor.headers(),
        json={"role": "member"},
    )
    assert resp.status_code == 409
    assert "owner" in resp.json()["error"]["message"]


async def test_change_role_owner_only(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """PATCH by a non-owner → 403 (only owners can manage members)."""
    other = await _new_home(db_engine)
    await _add_member(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=other.id,
        role=HomeRole.MEMBER,
    )

    from app.services.security import create_access_token

    headers = {
        "Authorization": f"Bearer {create_access_token(other.id)}",
        "X-Home-Id": str(seeded_actor.home_id),
    }
    resp = api_client.patch(
        f"/api/v1/homes/{seeded_actor.home_id}/members/{seeded_actor.user_id}",
        headers=headers,
        json={"role": "member"},
    )
    assert resp.status_code == 403


# --------------------------------------------------------------- remove


async def test_remove_member_happy_path(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """Removing a member returns 200 + the last snapshot of the row."""
    friend = await _make_user(db_engine)
    api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=seeded_actor.headers(),
        json={"email": friend.email, "role": "member"},
    )

    resp = api_client.delete(
        f"/api/v1/homes/{seeded_actor.home_id}/members/{friend.id}",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == str(friend.id)
    assert body["role"] == "member"

    # The list now has one row again (just the owner).
    list_resp = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=seeded_actor.headers(),
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


async def test_remove_last_owner_is_409(
    api_client: TestClient, seeded_actor
) -> None:
    """Removing the only owner → 409."""
    resp = api_client.delete(
        f"/api/v1/homes/{seeded_actor.home_id}/members/{seeded_actor.user_id}",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 409
    assert "owner" in resp.json()["error"]["message"]


async def test_remove_member_owner_only(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """DELETE by a non-owner → 403."""
    other = await _new_home(db_engine)
    await _add_member(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=other.id,
        role=HomeRole.MEMBER,
    )

    from app.services.security import create_access_token

    headers = {
        "Authorization": f"Bearer {create_access_token(other.id)}",
        "X-Home-Id": str(seeded_actor.home_id),
    }
    resp = api_client.delete(
        f"/api/v1/homes/{seeded_actor.home_id}/members/{seeded_actor.user_id}",
        headers=headers,
    )
    assert resp.status_code == 403


async def test_remove_member_unknown_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    """DELETE on an unknown user_id → 404 not_found."""
    resp = api_client.delete(
        f"/api/v1/homes/{seeded_actor.home_id}/members/{uuid.uuid4()}",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


async def test_remove_without_credentials_is_401(
    api_client: TestClient, seeded_actor
) -> None:
    """``Authorization`` missing but ``X-Home-Id`` present → 401.

    Same selector-but-no-bearer shape as :func:`test_list_members_no_credentials_is_401`
    — see the comment there for why we don't just send no headers at all.
    """
    resp = api_client.delete(
        f"/api/v1/homes/{seeded_actor.home_id}/members/{seeded_actor.user_id}",
        headers={"X-Home-Id": str(seeded_actor.home_id)},
    )
    assert resp.status_code == 401


# --------------------------------------------------------------- cross-home


async def test_invite_cross_home_is_404(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """A second user naming a home they don't belong to → 404 (not 403)."""
    other = await _new_home(db_engine)

    from app.services.security import create_access_token

    headers = {
        "Authorization": f"Bearer {create_access_token(other.id)}",
        "X-Home-Id": str(seeded_actor.home_id),  # not their home
    }
    resp = api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/members",
        headers=headers,
        json={"email": "x@example.com", "role": "member"},
    )
    assert resp.status_code == 404


# Silence unused-import warning while keeping the symbol handy for tests
# that might want to use it.
_ = SeededActor
