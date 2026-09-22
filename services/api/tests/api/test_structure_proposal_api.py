"""Integration tests for ``POST /api/v1/structures/propose``.

The load-bearing assertion in this file is the row-count one: a proposal must
persist **nothing** except its ``AgentTrace``. Checking only that the response
looks right would also pass for a call that quietly created the tree, and the
whole design of this batch is that the user confirms before anything is
written.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.providers.mock import MockAIProvider
from app.api.v1.structure import _get_ai_provider
from app.main import app
from app.models import AgentTrace
from app.models.room import Room
from app.models.storage import StorageSection, StorageSlot, StorageUnit

pytestmark = pytest.mark.asyncio

STORAGE_TABLES = {
    "rooms": Room,
    "storage_units": StorageUnit,
    "storage_sections": StorageSection,
    "storage_slots": StorageSlot,
    "agent_traces": AgentTrace,
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


# -------------------------------------------------------------------- text


async def test_text_proposal_persists_nothing_but_a_trace(
    api_client, seeded_actor, db_engine
) -> None:
    mock = MockAIProvider(structured_output_response=_proposal())
    _override_provider(mock)
    before = await _counts(db_engine)

    resp = api_client.post(
        "/api/v1/structures/propose",
        json={"description": "我家厨房有个三层吊柜"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text

    after = await _counts(db_engine)
    for table in ("rooms", "storage_units", "storage_sections", "storage_slots"):
        assert after[table] == before[table] == 0, f"{table} was written by a proposal"
    assert after["agent_traces"] == before["agent_traces"] + 1

    body = resp.json()
    assert body["source"] == "text"
    assert body["trace_id"] is not None
    assert body["proposal"]["rooms"][0]["name"] == "厨房"
    # StrEnum fields must serialize as their bare value, not "RoomType.KITCHEN".
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
    # No image means the text model — sending an empty image part would 400.
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

    # The model must never be handed a URL it cannot reach — the bytes ride
    # along as a data: URI.
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


async def test_the_template_branch_never_calls_a_model(
    api_client, seeded_actor, db_engine
) -> None:
    """No input → the server-side skeleton, at zero cost.

    ``structured_output_response`` is left unset, so any call to the provider
    raises ``RuntimeError`` and would surface as a 500 — the request succeeding
    is itself evidence that no call was made.
    """
    mock = MockAIProvider()
    _override_provider(mock)
    before = await _counts(db_engine)

    resp = api_client.post(
        "/api/v1/structures/propose", json={}, headers=seeded_actor.headers()
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["source"] == "template"
    assert body["trace_id"] is None
    assert mock.call_count == 0
    assert len(body["proposal"]["rooms"]) == 3

    after = await _counts(db_engine)
    assert after["agent_traces"] == before["agent_traces"]
    for table in ("rooms", "storage_units", "storage_sections", "storage_slots"):
        assert after[table] == before[table] == 0


async def test_the_template_needs_no_repair_of_its_own(
    api_client, seeded_actor
) -> None:
    """The template only ever names categories the home already has.

    On a brand-new home that means the sole warning is the first-run note —
    never a ``category_cleared`` for something the template invented itself.
    """
    _override_provider(MockAIProvider())
    resp = api_client.post(
        "/api/v1/structures/propose", json={}, headers=seeded_actor.headers()
    )
    assert [w["kind"] for w in resp.json()["warnings"]] == ["empty_vocabulary"]


# --------------------------------------------------------------- validation


async def test_a_ungrounded_category_comes_back_as_a_warning(
    api_client, seeded_actor
) -> None:
    """Step 4 rewrites the model's answer, so the user must be told.

    A brand-new home has no category vocabulary at all, which is exactly the
    first-run case — and the reason ``empty_vocabulary`` exists as a distinct
    note rather than looking like a bug.
    """
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
    """A JSON-write slip must not cost the user their home."""
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


async def test_an_invalid_enum_exhausts_the_retries(api_client, seeded_actor) -> None:
    """"厨房" is not a room_type — the schema rejects it, so it is a retry.

    Unlike an ungrounded category it is not something a rule can repair, so
    exhausting the retries and failing is the honest answer. Two parse retries
    after the first attempt — the same budget the vision path uses.
    """
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


async def test_propose_without_credentials_is_401(
    api_client, seeded_actor
) -> None:
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
