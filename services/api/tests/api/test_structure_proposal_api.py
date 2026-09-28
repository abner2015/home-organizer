"""Integration tests for ``/api/v1/structures/proposals/*``.

P1.3: ``POST /structures/propose`` writes the proposal to
``structure_proposals`` (status=pending) before returning, and the user
accepts it via ``POST /proposals/{id}/accept`` — which materialises the
4-level tree in one transaction (all-or-nothing on duplicate slot codes).
The original "propose persists nothing but the trace" load-bearing
assertion is reframed: storage tables stay empty after propose, but the
``structure_proposals`` table grows by 1.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.provider import StructureProposalOutput
from app.ai.providers.mock import MockAIProvider
from app.api.v1.structure import _get_ai_provider
from app.main import app
from app.models import AgentTrace, StructureProposal
from app.models.home import Home, HomeMembership
from app.models.room import Room
from app.models.storage import StorageSection, StorageSlot, StorageUnit
from app.models.user import User

pytestmark = pytest.mark.asyncio

STORAGE_TABLES = {
    "rooms": Room,
    "storage_units": StorageUnit,
    "storage_sections": StorageSection,
    "storage_slots": StorageSlot,
    "agent_traces": AgentTrace,
    "structure_proposals": StructureProposal,
}


# ------------------------------------------------------------------- helpers


def _override_provider(mock: MockAIProvider) -> None:
    """Point the endpoint at ``mock``.

    Keyed by the function the route actually ``Depends`` on — overriding
    ``get_provider`` underneath it would have no effect.
    """

    def _get() -> MockAIProvider:
        return mock

    app.dependency_overrides[_get_ai_provider] = _get


async def _counts(db_engine) -> dict[str, int]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        return {
            name: int(
                (
                    await session.execute(select(func.count()).select_from(model))
                ).scalar_one()
            )
            for name, model in STORAGE_TABLES.items()
        }


def _slot(code: str = "K1", categories: list[str] | None = None) -> dict:
    return {
        "code": code,
        "label": "左侧",
        "allowed_categories": categories or [],
        "capacity_hint": "medium",
    }


def _proposal(*, slots: list[dict] | None = None, room_type: str = "kitchen") -> dict:
    return {
        "rooms": [
            {
                "name": "厨房",
                "room_type": room_type,
                "units": [
                    {
                        "name": "吊柜",
                        "unit_type": "cabinet",
                        "sections": [
                            {
                                "name": "第1层",
                                "section_type": "layer",
                                "slots": slots or [_slot()],
                            }
                        ],
                    }
                ],
            }
        ],
        "rationale": "描述里提到厨房的三层吊柜",
        "confidence": 0.8,
    }


async def _upload_asset(client: TestClient, actor, body: bytes) -> str:
    resp = client.post(
        "/api/v1/assets/upload",
        headers=actor.headers(),
        files={"file": ("room.png", body, "image/png")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["asset_id"]


async def _propose(
    api_client, actor, *, description: str | None = None, asset_id: str | None = None
) -> tuple[int, dict]:
    payload: dict = {}
    if description is not None:
        payload["description"] = description
    if asset_id is not None:
        payload["asset_id"] = asset_id
    resp = api_client.post(
        "/api/v1/structures/propose", json=payload, headers=actor.headers()
    )
    return resp.status_code, resp.json()


# -------------------------------------------------------------------- text


async def test_text_proposal_persists_a_pending_proposal_and_a_trace(
    api_client, seeded_actor, db_engine
) -> None:
    """P1.3: propose writes one ``structure_proposals`` row + one ``agent_traces``
    row, but **no** rooms/units/sections/slots — those only land on accept."""
    mock = MockAIProvider(structured_output_response=_proposal())
    _override_provider(mock)
    before = await _counts(db_engine)

    status, body = await _propose(
        api_client, seeded_actor, description="我家厨房有个三层吊柜"
    )
    assert status == 200, body

    after = await _counts(db_engine)
    for table in ("rooms", "storage_units", "storage_sections", "storage_slots"):
        assert after[table] == before[table] == 0, f"{table} was written by a proposal"
    assert after["agent_traces"] == before["agent_traces"] + 1
    assert after["structure_proposals"] == before["structure_proposals"] + 1

    assert body["source"] == "text"
    assert body["trace_id"] is not None
    assert body["proposal_id"] is not None
    assert body["proposal"]["rooms"][0]["name"] == "厨房"
    assert body["proposal"]["rooms"][0]["room_type"] == "kitchen"


async def test_the_description_reaches_the_prompt(api_client, seeded_actor) -> None:
    mock = MockAIProvider(structured_output_response=_proposal())
    _override_provider(mock)

    api_client.post(
        "/api/v1/structures/propose",
        json={"description": "我家厨房有个三层吊柜"},
        headers=seeded_actor.headers(),
    )

    prompt = mock.recorded_calls[-1][1]["prompt"]
    assert "我家厨房有个三层吊柜" in prompt
    assert mock.last_image_url is None
    assert mock.last_model == mock.chat_model


# ------------------------------------------------------------------ photo


async def test_photo_proposal_inlines_the_image_and_uses_the_vision_model(
    api_client, seeded_actor
) -> None:
    from tests.api.conftest import make_png_bytes

    mock = MockAIProvider(structured_output_response=_proposal())
    _override_provider(mock)
    asset_id = await _upload_asset(api_client, seeded_actor, make_png_bytes())

    resp = api_client.post(
        "/api/v1/structures/propose",
        json={"asset_id": asset_id},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "photo"
    assert resp.json()["proposal_id"] is not None
    assert mock.last_image_url is not None
    assert mock.last_image_url.startswith("data:image/")
    assert mock.last_model == mock.vision_model


async def test_a_caption_rides_along_with_the_photo(api_client, seeded_actor) -> None:
    from tests.api.conftest import make_png_bytes

    mock = MockAIProvider(structured_output_response=_proposal())
    _override_provider(mock)
    asset_id = await _upload_asset(api_client, seeded_actor, make_png_bytes())

    resp = api_client.post(
        "/api/v1/structures/propose",
        json={"asset_id": asset_id, "description": "这是厨房"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "photo"
    assert "这是厨房" in mock.recorded_calls[-1][1]["prompt"]


async def test_proposing_for_an_unknown_asset_is_404(
    api_client, seeded_actor
) -> None:
    _override_provider(MockAIProvider(structured_output_response=_proposal()))
    resp = api_client.post(
        "/api/v1/structures/propose",
        json={"asset_id": str(uuid.uuid4())},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


# --------------------------------------------------------------- template


async def test_the_template_branch_persists_a_proposal_row_and_never_calls_a_model(
    api_client, seeded_actor, db_engine
) -> None:
    """No input → the server-side skeleton, at zero cost. P1.3 still writes
    a ``structure_proposals`` row so the user can accept / reject it later.
    """
    mock = MockAIProvider()
    _override_provider(mock)
    before = await _counts(db_engine)

    status, body = await _propose(api_client, seeded_actor)
    assert status == 200, body

    assert body["source"] == "template"
    assert body["trace_id"] is None
    assert body["proposal_id"] is not None
    assert mock.call_count == 0
    assert len(body["proposal"]["rooms"]) == 3

    after = await _counts(db_engine)
    assert after["agent_traces"] == before["agent_traces"]
    assert after["structure_proposals"] == before["structure_proposals"] + 1
    for table in ("rooms", "storage_units", "storage_sections", "storage_slots"):
        assert after[table] == before[table] == 0


async def test_the_template_needs_no_repair_of_its_own(
    api_client, seeded_actor
) -> None:
    _override_provider(MockAIProvider())
    resp = api_client.post(
        "/api/v1/structures/propose", json={}, headers=seeded_actor.headers()
    )
    assert [w["kind"] for w in resp.json()["warnings"]] == ["empty_vocabulary"]


# --------------------------------------------------------------- validation


async def test_a_ungrounded_category_comes_back_as_a_warning(
    api_client, seeded_actor
) -> None:
    mock = MockAIProvider(
        structured_output_response=_proposal(slots=[_slot(categories=["spaceship"])])
    )
    _override_provider(mock)

    resp = api_client.post(
        "/api/v1/structures/propose",
        json={"description": "厨房"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    kinds = [warning["kind"] for warning in body["warnings"]]
    assert "empty_vocabulary" in kinds
    assert "category_cleared" in kinds
    slot = body["proposal"]["rooms"][0]["units"][0]["sections"][0]["slots"][0]
    assert slot["allowed_categories"] == []


async def test_a_duplicate_code_is_dropped_and_reported(
    api_client, seeded_actor
) -> None:
    mock = MockAIProvider(
        structured_output_response=_proposal(slots=[_slot("K1"), _slot("K1")])
    )
    _override_provider(mock)

    resp = api_client.post(
        "/api/v1/structures/propose",
        json={"description": "厨房"},
        headers=seeded_actor.headers(),
    )
    body = resp.json()
    assert "duplicate_code" in [w["kind"] for w in body["warnings"]]
    slots = body["proposal"]["rooms"][0]["units"][0]["sections"][0]["slots"]
    assert len(slots) == 1


# ------------------------------------------------------------------ retries


async def test_a_bad_reply_is_retried_once_then_succeeds(
    api_client, seeded_actor, db_engine
) -> None:
    mock = MockAIProvider(
        structured_output_responses=[{"rooms": "not a list"}, _proposal()]
    )
    _override_provider(mock)

    resp = api_client.post(
        "/api/v1/structures/propose",
        json={"description": "厨房"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    assert mock.call_count == 2

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        trace = (await session.execute(select(AgentTrace))).scalar_one()
        assert [step["parse_ok"] for step in trace.steps] == [False, True]
        assert trace.final_status == "success"
        assert trace.item_id is None

        # Exactly one proposal row was written, despite the parse-then-retry.
        proposals = (
            await session.execute(select(StructureProposal))
        ).scalars().all()
        assert len(proposals) == 1
        assert proposals[0].status == "pending"


async def test_an_invalid_enum_exhausts_the_retries(
    api_client, seeded_actor, db_engine
) -> None:
    mock = MockAIProvider(
        structured_output_responses=[
            _proposal(room_type="厨房"),
            _proposal(room_type="厨房"),
            _proposal(room_type="厨房"),
        ]
    )
    _override_provider(mock)

    resp = api_client.post(
        "/api/v1/structures/propose",
        json={"description": "厨房"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "ai_output_parse_error"
    assert mock.call_count == 3


# --------------------------------------------------------------------- auth


async def test_propose_without_credentials_is_401(api_client, seeded_actor) -> None:
    resp = api_client.post(
        "/api/v1/structures/propose",
        json={"description": "厨房"},
        headers={"X-Home-Id": str(seeded_actor.home_id)},
    )
    assert resp.status_code == 401


async def test_an_unknown_field_is_rejected(api_client, seeded_actor) -> None:
    resp = api_client.post(
        "/api/v1/structures/propose",
        json={"description": "厨房", "rooms": []},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 422


# --------------------------------------------------------------- accept (P1.3)


async def test_propose_returns_proposal_id(api_client, seeded_actor) -> None:
    """The route's response now carries ``proposal_id`` (the row's UUID)."""
    _override_provider(MockAIProvider(structured_output_response=_proposal()))
    _, body = await _propose(api_client, seeded_actor, description="厨房")
    assert "proposal_id" in body
    uuid.UUID(body["proposal_id"])  # raises if not a valid UUID


async def test_accept_creates_full_tree_atomically(
    api_client, seeded_actor, db_engine
) -> None:
    """Accept materialises every level in one transaction."""
    _override_provider(MockAIProvider(structured_output_response=_proposal()))
    _, proposal_body = await _propose(api_client, seeded_actor, description="厨房")
    proposal_id = proposal_body["proposal_id"]

    resp = api_client.post(
        f"/api/v1/structures/proposals/{proposal_id}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["proposal_id"] == proposal_id
    assert body["counts"] == {"rooms": 1, "units": 1, "sections": 1, "slots": 1}

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rooms = (await session.execute(select(Room))).scalars().all()
        assert len(rooms) == 1
        assert rooms[0].name == "厨房"
        assert rooms[0].room_type == "kitchen"

        proposal = (
            await session.execute(
                select(StructureProposal).where(StructureProposal.id == uuid.UUID(proposal_id))
            )
        ).scalar_one()
        assert proposal.status == "accepted"
        assert proposal.accepted_at is not None


async def test_accept_with_duplicate_slot_code_returns_409_nothing_created(
    api_client, seeded_actor, db_engine
) -> None:
    """Two slots with identical ``(section_id, code)`` in the same proposal —
    the second ``create_slot`` call raises ``ConflictError`` → 409. Step 4
    had already dropped one for the user-visible proposals; this test bypasses
    that validator to verify the *atomic* guarantee directly.

    Nothing is persisted (transaction rollback).
    """
    from app.ai.provider import StructureProposalOutput
    from app.services import structure_proposal_service

    # Construct a JSONB blob the service will walk: room → unit → section →
    # two slots both carrying ``code="DUP"``.
    dup_proposal = {
        "rooms": [
            {
                "name": "厨房",
                "room_type": "kitchen",
                "units": [
                    {
                        "name": "吊柜",
                        "unit_type": "cabinet",
                        "sections": [
                            {
                                "name": "第1层",
                                "section_type": "layer",
                                "slots": [
                                    {
                                        "code": "DUP",
                                        "label": "a",
                                        "allowed_categories": [],
                                        "capacity_hint": None,
                                    },
                                    {
                                        "code": "DUP",
                                        "label": "b",
                                        "allowed_categories": [],
                                        "capacity_hint": None,
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
        "rationale": "x",
        "confidence": 0.5,
    }
    out = StructureProposalOutput.model_validate(dup_proposal)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        row = await structure_proposal_service._persist_proposal(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            source="text",
            asset_id=None,
            description="dup",
            proposal=out,
            warnings=[],
            trace_id=None,
        )
        await session.commit()
        proposal_id = str(row.id)

    before = await _counts(db_engine)
    resp = api_client.post(
        f"/api/v1/structures/proposals/{proposal_id}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 409, resp.text

    after = await _counts(db_engine)
    for table in ("rooms", "storage_units", "storage_sections", "storage_slots"):
        assert after[table] == before[table], f"{table} was created despite 409"

    async with factory() as session:
        row = (
            await session.execute(
                select(StructureProposal).where(
                    StructureProposal.id == uuid.UUID(proposal_id)
                )
            )
        ).scalar_one()
        assert row.status == "pending"
        assert row.accepted_at is None


async def test_accept_with_existing_room_name_succeeds(
    api_client, seeded_actor, db_engine
) -> None:
    """Name is **not** unique — a renamed room can share a name with a new
    one. Accept must not refuse just because the proposed name exists."""
    _override_provider(MockAIProvider(structured_output_response=_proposal()))
    # Seed an existing 厨房.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Room(
                id=uuid.uuid4(),
                home_id=seeded_actor.home_id,
                name="厨房",
                room_type="kitchen",
                sort_order=0,
            )
        )
        await session.commit()

    _, proposal_body = await _propose(api_client, seeded_actor, description="再来一个厨房")
    proposal_id = proposal_body["proposal_id"]

    resp = api_client.post(
        f"/api/v1/structures/proposals/{proposal_id}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    async with factory() as session:
        rooms = (
            await session.execute(select(Room).where(Room.home_id == seeded_actor.home_id))
        ).scalars().all()
        assert len(rooms) == 2
        assert all(r.name == "厨房" for r in rooms)


# --------------------------------------------------------------- reject (P1.3)


async def test_reject_marks_rejected_with_note(api_client, seeded_actor, db_engine) -> None:
    _override_provider(MockAIProvider(structured_output_response=_proposal()))
    _, proposal_body = await _propose(api_client, seeded_actor, description="厨房")
    proposal_id = proposal_body["proposal_id"]

    resp = api_client.post(
        f"/api/v1/structures/proposals/{proposal_id}/reject",
        json={"note": "  厨房已有  "},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"
    assert resp.json()["rejection_note"] == "厨房已有"

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(StructureProposal).where(
                    StructureProposal.id == uuid.UUID(proposal_id)
                )
            )
        ).scalar_one()
        assert row.status == "rejected"
        assert row.rejection_note == "厨房已有"
        assert row.rejected_at is not None


async def test_reject_twice_returns_409(api_client, seeded_actor) -> None:
    _override_provider(MockAIProvider(structured_output_response=_proposal()))
    _, body = await _propose(api_client, seeded_actor, description="厨房")
    pid = body["proposal_id"]
    first = api_client.post(
        f"/api/v1/structures/proposals/{pid}/reject",
        json={},
        headers=seeded_actor.headers(),
    )
    assert first.status_code == 200
    second = api_client.post(
        f"/api/v1/structures/proposals/{pid}/reject",
        json={},
        headers=seeded_actor.headers(),
    )
    assert second.status_code == 409


# ----------------------------------------------------------- list / get (P1.3)


async def test_list_proposals_filter_by_status(api_client, seeded_actor) -> None:
    """Three proposals: accept one, reject one, leave one pending. The pending
    filter returns the third only."""
    _override_provider(MockAIProvider(structured_output_response=_proposal()))

    _, acc_body = await _propose(api_client, seeded_actor, description="a")
    api_client.post(
        f"/api/v1/structures/proposals/{acc_body['proposal_id']}/accept",
        json={},
        headers=seeded_actor.headers(),
    )

    _, rej_body = await _propose(api_client, seeded_actor, description="b")
    api_client.post(
        f"/api/v1/structures/proposals/{rej_body['proposal_id']}/reject",
        json={"note": "no"},
        headers=seeded_actor.headers(),
    )

    _, pend_body = await _propose(api_client, seeded_actor, description="c")

    all_resp = api_client.get(
        "/api/v1/structures/proposals", headers=seeded_actor.headers()
    )
    assert all_resp.status_code == 200
    all_rows = all_resp.json()
    assert len(all_rows) == 3
    # newest first → pending was created last
    assert all_rows[0]["id"] == pend_body["proposal_id"]

    pending = api_client.get(
        "/api/v1/structures/proposals?status=pending",
        headers=seeded_actor.headers(),
    ).json()
    assert [r["status"] for r in pending] == ["pending"]
    assert pending[0]["id"] == pend_body["proposal_id"]

    accepted = api_client.get(
        "/api/v1/structures/proposals?status=accepted",
        headers=seeded_actor.headers(),
    ).json()
    assert [r["status"] for r in accepted] == ["accepted"]


async def test_list_proposals_invalid_status_filter_returns_400(
    api_client, seeded_actor
) -> None:
    resp = api_client.get(
        "/api/v1/structures/proposals?status=garbage",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 400


async def test_list_proposals_only_returns_callers_home(
    api_client, seeded_actor, db_engine
) -> None:
    """An actor in Home A cannot see Home B's proposals via list."""
    from app.ai.provider import StructureProposalOutput
    from app.db.enums import HomeRole
    from app.services import structure_proposal_service

    other_user_id = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        other_user = User(
            id=other_user_id,
            email=f"o-{other_user_id.hex}@example.com",
            display_name="Other",
            password_hash="x",
        )
        other_home = Home(
            id=uuid.uuid4(),
            name="Other Home",
            owner_id=other_user_id,
        )
        session.add_all([other_user, other_home])
        await session.flush()
        session.add(
            HomeMembership(
                home_id=other_home.id,
                user_id=other_user_id,
                role=HomeRole.OWNER.value,
            )
        )
        await session.flush()
        out = StructureProposalOutput.model_validate(_proposal())
        await structure_proposal_service._persist_proposal(
            session,
            home_id=other_home.id,
            user_id=other_user_id,
            source="template",
            asset_id=None,
            description=None,
            proposal=out,
            warnings=[],
            trace_id=None,
        )
        await session.commit()

    resp = api_client.get(
        "/api/v1/structures/proposals", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200
    assert all(r["home_id"] == str(seeded_actor.home_id) for r in resp.json())


async def test_get_pending_proposal(api_client, seeded_actor) -> None:
    _override_provider(MockAIProvider(structured_output_response=_proposal()))
    _, body = await _propose(api_client, seeded_actor, description="看细节")
    pid = body["proposal_id"]

    resp = api_client.get(
        f"/api/v1/structures/proposals/{pid}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200
    detail = resp.json()
    # The detail endpoint emits ``id`` (matches the row's PK), not ``proposal_id``.
    assert detail["id"] == pid
    assert detail["status"] == "pending"
    assert detail["source"] == "text"
    assert detail["proposal"]["rooms"][0]["name"] == "厨房"


async def test_get_proposal_from_other_home_returns_404(
    api_client, seeded_actor, db_engine
) -> None:
    """Cross-home access is invisible — even the existence of the row is hidden."""
    from app.ai.provider import StructureProposalOutput
    from app.db.enums import HomeRole
    from app.services import structure_proposal_service

    other_user_id = uuid.uuid4()
    other_home_id = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        other_user = User(
            id=other_user_id,
            email=f"x-{other_user_id.hex}@example.com",
            display_name="X",
            password_hash="x",
        )
        other_home = Home(
            id=other_home_id, name="Other", owner_id=other_user_id
        )
        session.add_all([other_user, other_home])
        await session.flush()
        session.add(
            HomeMembership(
                home_id=other_home_id,
                user_id=other_user_id,
                role=HomeRole.OWNER.value,
            )
        )
        await session.flush()
        out = StructureProposalOutput.model_validate(_proposal())
        row = await structure_proposal_service._persist_proposal(
            session,
            home_id=other_home_id,
            user_id=other_user_id,
            source="template",
            asset_id=None,
            description=None,
            proposal=out,
            warnings=[],
            trace_id=None,
        )
        await session.commit()
        other_pid = str(row.id)

    resp = api_client.get(
        f"/api/v1/structures/proposals/{other_pid}",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


async def test_accept_proposal_from_other_home_returns_404(
    api_client, seeded_actor, db_engine
) -> None:
    from app.db.enums import HomeRole
    from app.services import structure_proposal_service

    other_user_id = uuid.uuid4()
    other_home_id = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        other_user = User(
            id=other_user_id,
            email=f"y-{other_user_id.hex}@example.com",
            display_name="Y",
            password_hash="x",
        )
        other_home = Home(
            id=other_home_id, name="Other2", owner_id=other_user_id
        )
        session.add_all([other_user, other_home])
        await session.flush()
        session.add(
            HomeMembership(
                home_id=other_home_id,
                user_id=other_user_id,
                role=HomeRole.OWNER.value,
            )
        )
        await session.flush()
        out = StructureProposalOutput.model_validate(_proposal())
        row = await structure_proposal_service._persist_proposal(
            session,
            home_id=other_home_id,
            user_id=other_user_id,
            source="text",
            asset_id=None,
            description="y",
            proposal=out,
            warnings=[],
            trace_id=None,
        )
        await session.commit()
        other_pid = str(row.id)

    resp = api_client.post(
        f"/api/v1/structures/proposals/{other_pid}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


async def test_accept_twice_returns_409_via_state_check(
    api_client, seeded_actor, db_engine
) -> None:
    """First accept goes through the API and succeeds. Second accept sees the
    row already accepted and returns 409."""
    _override_provider(MockAIProvider(structured_output_response=_proposal()))
    _, body = await _propose(api_client, seeded_actor, description="并发")
    pid = body["proposal_id"]

    first = api_client.post(
        f"/api/v1/structures/proposals/{pid}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert first.status_code == 200, first.text

    # The fastapi TestClient opens its own session per request, so a fresh
    # GET from it sees the committed 'accepted' state.
    second = api_client.post(
        f"/api/v1/structures/proposals/{pid}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert second.status_code == 409


async def test_proposal_referencing_deleted_asset_keeps_proposal_with_null_asset_id(
    api_client, seeded_actor, db_engine
) -> None:
    """``asset_id`` is FK ON DELETE SET NULL — deleting the asset doesn't
    cascade the proposal. SQLite default disables FK enforcement, so we
    simulate the cascade at the model layer (load → set NULL → commit) and
    verify the proposal survives.
    """
    from app.models import Asset

    _override_provider(MockAIProvider(structured_output_response=_proposal()))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        asset = Asset(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            created_by=seeded_actor.user_id,
            bucket="test-bucket",
            object_key="home/test/p.png",
            content_type="image/png",
            size_bytes=1,
            sha256="x" * 64,
            status="ready",
            width=1,
            height=1,
        )
        session.add(asset)
        await session.commit()
        asset_id = str(asset.id)

    # Propose against the asset we just created (bypass the /upload API to
    # stay local to the test).
    from app.ai.provider import StructureProposalOutput
    from app.services import structure_proposal_service

    async with factory() as session:
        # Build a templated proposal directly (no LLM needed).
        raw = StructureProposalOutput.model_validate(_proposal())
        row = await structure_proposal_service._persist_proposal(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            source="photo",
            asset_id=uuid.UUID(asset_id),
            description="x",
            proposal=raw,
            warnings=[],
            trace_id=None,
        )
        await session.commit()
        pid = str(row.id)

        # Simulate the FK SET NULL cascade: delete asset, null the FK.
        await session.execute(
            Asset.__table__.delete().where(Asset.id == uuid.UUID(asset_id))
        )
        row.asset_id = None
        await session.commit()

    detail = api_client.get(
        f"/api/v1/structures/proposals/{pid}", headers=seeded_actor.headers()
    ).json()
    assert detail["asset_id"] is None
    assert detail["status"] == "pending"
