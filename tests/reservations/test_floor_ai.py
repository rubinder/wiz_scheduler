"""AI floor-plan drafts from photos or a blueprint (#259 follow-up, decision 101).

The model is faked: `floor_ai.draft_model` is swapped for a scripted client,
and the background job's sessions come from the test engine."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend import database as _db_module
from backend.config import settings
from backend.models import Company, OwnershipGroup, TokenUsage, TokenUsageDaily
from backend.reservations import floor_ai
from backend.reservations.floor import validate_floor
from backend.reservations.floor_ai_models import FloorPlanDraft
from backend.services.data_retention import run_data_retention
from tests.conftest import COMPANY2_ID, COMPANY_ID, LOCATION_ID, get_test_session_factory

pytestmark = [pytest.mark.integration]

BASE = "/api/v1/reservations"
OG_ID = "ogfloor1"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64

GOOD = {
    "sections": [{"key": "main", "name": "Main room"}],
    "tables": [
        {"label": "T1", "section_key": "main", "min_seats": 1, "max_seats": 2,
         "combinable": True, "x": 2, "y": 2, "w": 2, "h": 2, "rotation": 0},
        {"label": "T2", "section_key": "main", "min_seats": 1, "max_seats": 2,
         "combinable": True, "x": 5, "y": 2, "w": 2, "h": 2, "rotation": 0},
        {"label": "B1", "section_key": None, "min_seats": 4, "max_seats": 6,
         "combinable": False, "x": 10, "y": 8, "w": 3, "h": 2, "rotation": 90},
    ],
    "combinations": [{"name": "T1+T2", "table_labels": ["T1", "T2"]}],
    "warnings": ["Seat counts guessed from table size."],
    "confidence": "medium",
}
USAGE = {"input_tokens": 50_000, "output_tokens": 4_000,
         "cache_read_tokens": 0, "cache_write_tokens": 0}
# 50k x $4/M + 4k x $20/M
COST = 0.28


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# What the fake's count_tokens reports: a worst case of
# 50k x $4/M + 16k x $20/M = $0.52, under the $1.00 floor.
COUNTED = 50_000


class FakeModel:
    def __init__(self, reply: "floor_ai.ModelReply | Exception") -> None:
        self.reply = reply
        self.calls: list[dict] = []
        self.counted: "int | Exception" = COUNTED
        self.delay = 0.0

    async def count_tokens(self, *, system: str, content: list[dict]) -> int:
        if isinstance(self.counted, Exception):
            raise self.counted
        return self.counted

    async def draft(self, *, system: str, content: list[dict]) -> "floor_ai.ModelReply":
        self.calls.append({"system": system, "content": content})
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _reply(body: object = GOOD, stop: str = "end_turn") -> floor_ai.ModelReply:
    text = body if isinstance(body, str) else json.dumps(body)
    return floor_ai.ModelReply(stop_reason=stop, text=text, usage=dict(USAGE))


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)
    monkeypatch.setattr(settings, "INCLUDED_LLM_USD", 0.0)
    monkeypatch.setattr(_db_module, "async_session_factory", get_test_session_factory())


@pytest.fixture
def model(monkeypatch) -> FakeModel:
    fake = FakeModel(_reply())
    monkeypatch.setattr(floor_ai, "draft_model", lambda: fake)
    return fake


async def _credits(db: AsyncSession, usd: float) -> None:
    db.add(OwnershipGroup(id=OG_ID, name="Floor OG", ai_credits_usd=usd))
    await db.flush()
    await db.execute(update(Company).where(Company.id == COMPANY_ID)
                     .values(ownership_group_id=OG_ID))
    await db.commit()


async def _og(db: AsyncSession) -> OwnershipGroup:
    return (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == OG_ID)
        .execution_options(populate_existing=True))).scalar_one()


def _files(*items: tuple[str, bytes, str]):
    return [("files", (name, data, ctype)) for name, data, ctype in items]


async def _post(client, token, files, captions=None, notes=None, location=LOCATION_ID):
    data: dict = {}
    if captions is not None:
        data["captions"] = captions
    if notes is not None:
        data["notes"] = notes
    return await client.post(f"{BASE}/locations/{location}/floor/ai-drafts",
                             files=files, data=data, headers=_auth(token))


# --- the whole flow ---------------------------------------------------------


@pytest.mark.asyncio
async def test_photos_and_blueprint_make_a_saveable_draft_charged_at_cost(
    client, db_session, manager_token, seed_location, model
):
    await _credits(db_session, 10.0)
    r = await _post(
        client, manager_token,
        _files(("a.jpg", JPEG, "image/jpeg"), ("plan.pdf", PDF, "application/pdf")),
        captions=["From the entrance, facing the bar", "The architect's plan"],
        notes="About 20 tables; the second photo is the back room.")
    assert r.status_code == 202, r.text
    draft_id = r.json()["id"]
    assert r.json()["status"] in ("queued", "running", "done")

    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/floor/ai-drafts/{draft_id}",
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done"
    assert body["confidence"] == "medium"
    assert body["warnings"] == ["Seat counts guessed from table size."]
    assert body["cost_usd"] == pytest.approx(COST)
    assert [t["label"] for t in body["result"]["tables"]] == ["T1", "T2", "B1"]

    # The draft is what the editor saves, unchanged.
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/floor", json=body["result"],
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert sorted(t["label"] for t in r.json()["tables"]) == ["B1", "T1", "T2"]

    # At cost: no markup, debited from credits, counted toward the daily cap.
    assert (await _og(db_session)).ai_credits_usd == pytest.approx(10.0 - COST)
    daily = (await db_session.execute(select(TokenUsageDaily).execution_options(
        populate_existing=True))).scalar_one()
    assert daily.cost_usd == pytest.approx(COST)
    monthly = (await db_session.execute(select(TokenUsage).execution_options(
        populate_existing=True))).scalar_one()
    assert monthly.charged_usd == pytest.approx(COST)

    # What the model saw: each file, then its caption, then notes and the task.
    content = model.calls[0]["content"]
    assert [b["type"] for b in content[:4]] == ["image", "text", "document", "text"]
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[2]["source"]["media_type"] == "application/pdf"
    assert "facing the bar" in content[1]["text"]
    assert "back room" in json.dumps(content[4:])
    assert "never instructions" in model.calls[0]["system"].lower()


@pytest.mark.asyncio
async def test_nothing_is_kept_of_the_files(client, db_session, manager_token,
                                            seed_location, model):
    await _credits(db_session, 10.0)
    await _post(client, manager_token, _files(("a.png", PNG, "image/png")),
                captions=["Patio"], notes="Two photos of the patio")
    row = (await db_session.execute(select(FloorPlanDraft))).scalar_one()
    stored = json.dumps([row.result, row.warnings, row.error_code])
    assert "Patio" not in stored and "patio" not in stored


@pytest.mark.parametrize("reply,code", [
    (_reply(stop="refusal"), "refused"),
    (_reply(stop="max_tokens"), "truncated"),
    (_reply("not json {"), "unreadable"),
    (_reply({"tables": "nope"}), "unreadable"),
    (RuntimeError("network down"), "model_error"),
])
@pytest.mark.asyncio
async def test_a_failed_call_is_not_charged(client, db_session, manager_token,
                                            seed_location, model, reply, code):
    await _credits(db_session, 10.0)
    model.reply = reply
    r = await _post(client, manager_token, _files(("a.webp", WEBP, "image/webp")))
    assert r.status_code == 202, r.text
    r = await client.get(
        f"{BASE}/locations/{LOCATION_ID}/floor/ai-drafts/{r.json()['id']}",
        headers=_auth(manager_token))
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_code"] == code
    assert body["result"] is None and body["cost_usd"] is None
    assert (await _og(db_session)).ai_credits_usd == pytest.approx(10.0)
    # Not charged, but a call that returned usage cost us: the daily cap
    # counts it. A call that raised has no usage to count.
    daily = (await db_session.execute(select(TokenUsageDaily).execution_options(
        populate_existing=True))).scalar_one_or_none()
    if code == "model_error":
        # No usage from a call that raised: the counted input is recorded.
        assert daily is not None and daily.cost_usd == pytest.approx(COUNTED * 4 / 1_000_000)
    else:
        assert daily is not None and daily.cost_usd == pytest.approx(COST)
    monthly = (await db_session.execute(select(TokenUsage))).scalar_one_or_none()
    assert monthly is None or monthly.charged_usd == 0


# --- billing gate -----------------------------------------------------------


@pytest.mark.asyncio
async def test_402_without_credits_makes_no_row_and_no_call(
    client, db_session, manager_token, seed_location, model
):
    await _credits(db_session, 0.0)
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "ai_credits_required"
    assert model.calls == []
    assert (await db_session.execute(select(FloorPlanDraft))).first() is None


@pytest.mark.asyncio
async def test_402_on_billing_hold(client, db_session, manager_token, seed_location, model):
    await _credits(db_session, 10.0)
    og = await _og(db_session)
    og.autoreload_failed_at = datetime.now(timezone.utc)
    await db_session.commit()
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "billing_on_hold"
    assert model.calls == []


@pytest.mark.asyncio
async def test_402_over_the_daily_cost_cap(client, db_session, manager_token,
                                           seed_location, model):
    await _credits(db_session, 100.0)
    db_session.add(TokenUsageDaily(
        ownership_group_id=OG_ID, usage_date=datetime.now(timezone.utc).date(),
        input_tokens=1, output_tokens=1, total_tokens=2,
        cost_usd=settings.OG_ANTHROPIC_DAILY_CAP_USD))
    await db_session.commit()
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "daily_cost_cap_exceeded"
    assert model.calls == []


@pytest.mark.asyncio
async def test_402_without_an_ownership_group(client, db_session, manager_token,
                                              seed_location, model):
    """No group means no credits to debit: refuse rather than run free."""
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "ai_credits_required"
    assert model.calls == []


@pytest.mark.asyncio
async def test_402_below_the_worst_case_draft_cost(client, db_session, manager_token,
                                                   seed_location, model, monkeypatch):
    """A cent of credit must not buy a dollar of model time: the balance must
    cover a worst-case draft. The debit itself stays at actual cost."""
    monkeypatch.setattr(settings, "FLOOR_PLAN_AI_MIN_CREDITS_USD", 1.0)
    await _credits(db_session, 0.99)
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "ai_credits_required"
    assert model.calls == []

    og = await _og(db_session)
    og.ai_credits_usd = 1.0
    await db_session.commit()
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 202, r.text
    assert (await _og(db_session)).ai_credits_usd == pytest.approx(1.0 - COST)


@pytest.mark.asyncio
async def test_included_allowance_counts_toward_the_minimum(
    client, db_session, manager_token, seed_location, model, monkeypatch
):
    monkeypatch.setattr(settings, "FLOOR_PLAN_AI_MIN_CREDITS_USD", 1.0)
    monkeypatch.setattr(settings, "INCLUDED_LLM_USD", 0.6)
    await _credits(db_session, 0.5)
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 202, r.text


def test_the_minimum_covers_a_worst_case_call():
    worst_output = (settings.FLOOR_PLAN_AI_MAX_TOKENS
                    * settings.FLOOR_PLAN_AI_OUTPUT_USD_PER_MTOK / 1_000_000)
    assert settings.FLOOR_PLAN_AI_MIN_CREDITS_USD > worst_output


# --- one draft at a time ----------------------------------------------------


@pytest.mark.asyncio
async def test_409_while_a_draft_is_in_progress(client, db_session, manager_token,
                                                seed_location, model):
    await _credits(db_session, 10.0)
    await _row(db_session, status="running")
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "draft_in_progress"
    assert model.calls == []


@pytest.mark.asyncio
async def test_a_timed_out_or_finished_draft_does_not_block(
    client, db_session, manager_token, seed_location, model
):
    await _credits(db_session, 10.0)
    await _row(db_session, status="running", age=timedelta(minutes=6))
    await _row(db_session, status="done")
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_drafts_running_elsewhere_in_the_group_need_their_own_credit(
    client, db_session, manager_token, seed_location, seed_region, model, monkeypatch
):
    """A balance that covers one draft must not start one per location: the
    gate counts the group's drafts in flight."""
    from backend.models import Location

    monkeypatch.setattr(settings, "FLOOR_PLAN_AI_MIN_CREDITS_USD", 1.0)
    await _credits(db_session, 1.5)
    other = Location(company_id=COMPANY_ID, region_id=seed_region.id, name="Uptown",
                     timezone="America/New_York")
    db_session.add(other)
    await db_session.commit()
    await _row(db_session, location=other.id, status="running")

    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["code"] == "ai_credits_required"
    assert model.calls == []

    og = await _og(db_session)
    og.ai_credits_usd = 2.0
    await db_session.commit()
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 202, r.text


# --- worst-case estimate ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_balance_must_cover_the_counted_worst_case(
    client, db_session, manager_token, seed_location, model
):
    """A big blueprint can cost more than the $1.00 floor: the request is
    counted first, and the balance must cover its input plus max_tokens."""
    model.counted = 500_000  # $2.00 input + $0.32 output
    await _credits(db_session, 2.0)
    r = await _post(client, manager_token, _files(("p.pdf", PDF, "application/pdf")))
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["code"] == "ai_credits_required"
    assert detail["estimate_usd"] == pytest.approx(2.32)
    assert model.calls == []

    og = await _og(db_session)
    og.ai_credits_usd = 2.5
    await db_session.commit()
    r = await _post(client, manager_token, _files(("p.pdf", PDF, "application/pdf")))
    assert r.status_code == 202, r.text
    row = (await db_session.execute(select(FloorPlanDraft).execution_options(
        populate_existing=True))).scalar_one()
    assert float(row.reserved_usd) == pytest.approx(2.32)


@pytest.mark.asyncio
async def test_in_flight_drafts_reserve_their_own_estimate(
    client, db_session, manager_token, seed_location, seed_region, model
):
    from backend.models import Location

    await _credits(db_session, 4.0)
    other = Location(company_id=COMPANY_ID, region_id=seed_region.id, name="Uptown",
                     timezone="America/New_York")
    db_session.add(other)
    await db_session.commit()
    busy = await _row(db_session, location=other.id, status="running")
    busy.reserved_usd = 3.5
    await db_session.commit()
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 402, r.text  # 3.50 + 1.00 > 4.00


@pytest.mark.asyncio
async def test_503_when_the_request_cannot_be_counted(
    client, db_session, manager_token, seed_location, model
):
    """Fail closed: no estimate, no call."""
    await _credits(db_session, 10.0)
    model.counted = RuntimeError("count failed")
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "estimate_unavailable"
    assert model.calls == []
    assert (await db_session.execute(select(FloorPlanDraft))).first() is None


@pytest.mark.asyncio
async def test_pdfs_are_capped_at_50_pages(client, db_session, manager_token, seed_location, model):
    await _credits(db_session, 10.0)
    pages = b"".join(b"%d 0 obj << /Type /Page /Parent 2 0 R >> endobj\n" % i for i in range(30))
    pdf = PDF + b"1 0 obj << /Type /Pages /Count 30 >> endobj\n" + pages
    r = await _post(client, manager_token, _files(("a.pdf", pdf, "application/pdf"),
                                                  ("b.pdf", pdf, "application/pdf")))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "too_many_pages"
    r = await _post(client, manager_token, _files(("a.pdf", pdf, "application/pdf")))
    assert r.status_code == 202, r.text


def test_pdf_page_count():
    assert floor_ai.pdf_page_count(PDF + b"<< /Type /Pages /Count 2 >> << /Type /Page >> "
                                         b"<</Type/Page/Parent 1 0 R>>") == 2


# --- failure limit ------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_after_three_failed_drafts_today(
    client, db_session, manager_token, seed_location, model
):
    await _credits(db_session, 10.0)
    for _ in range(2):
        row = await _row(db_session, status="failed")
        row.error_code = "unreadable"
    await _row(db_session, status="failed", age=timedelta(days=1, hours=1))
    await db_session.commit()
    model.reply = _reply(stop="refusal")
    # The third failure today (it finishes before the response is read).
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 202, r.text
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "too_many_failed_drafts"
    assert len(model.calls) == 1


# --- the model call's deadline -------------------------------------------------


@pytest.mark.asyncio
async def test_a_call_past_the_deadline_fails_uncharged(
    client, db_session, manager_token, seed_location, model, monkeypatch
):
    monkeypatch.setattr(floor_ai, "MODEL_DEADLINE_S", 0.05)
    model.delay = 1.0
    await _credits(db_session, 10.0)
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    body = (await client.get(
        f"{BASE}/locations/{LOCATION_ID}/floor/ai-drafts/{r.json()['id']}",
        headers=_auth(manager_token))).json()
    assert body["status"] == "failed" and body["error_code"] == "timeout"
    assert (await _og(db_session)).ai_credits_usd == pytest.approx(10.0)
    daily = (await db_session.execute(select(TokenUsageDaily))).scalar_one()
    assert daily.cost_usd == pytest.approx(COUNTED * 4 / 1_000_000)


def test_the_deadline_ends_before_the_slot_is_released():
    assert floor_ai.MODEL_DEADLINE_S <= floor_ai.DRAFT_TIMEOUT.total_seconds() - 30


def test_the_sdk_client_does_not_retry(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    assert floor_ai.AnthropicFloorDraftModel()._sdk().max_retries == 0


# --- resuming ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_latest_is_the_newest_draft_at_the_location(
    client, db_session, manager_token, seed_location, seed_region
):
    from backend.models import Location

    h = _auth(manager_token)
    url = f"{BASE}/locations/{LOCATION_ID}/floor/ai-drafts/latest"
    assert (await client.get(url, headers=h)).status_code == 404
    other = Location(company_id=COMPANY_ID, region_id=seed_region.id, name="Uptown",
                     timezone="America/New_York")
    db_session.add(other)
    await db_session.commit()
    await _row(db_session, status="done", age=timedelta(minutes=30))
    newest = await _row(db_session, status="running", age=timedelta(minutes=1))
    await _row(db_session, location=other.id, status="queued")
    body = (await client.get(url, headers=h)).json()
    assert body["id"] == newest.id and body["status"] == "running"


@pytest.mark.asyncio
async def test_latest_reads_a_stuck_draft_as_timed_out(client, db_session, manager_token,
                                                       seed_location):
    await _row(db_session, status="running", age=timedelta(minutes=6))
    body = (await client.get(f"{BASE}/locations/{LOCATION_ID}/floor/ai-drafts/latest",
                             headers=_auth(manager_token))).json()
    assert body["status"] == "failed" and body["error_code"] == "timeout"


# --- location scope -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_regular_manager_drafts_only_at_their_locations(
    client, db_session, regular_token, seed_location, model
):
    from tests.conftest import OTHER_LOCATION_ID

    await _credits(db_session, 10.0)
    r = await _post(client, regular_token, _files(("a.jpg", JPEG, "image/jpeg")),
                    location=OTHER_LOCATION_ID)
    assert r.status_code == 404
    assert model.calls == []
    r = await _post(client, regular_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 202, r.text


# --- before the body is parsed ---------------------------------------------------


async def _raw_post(headers: list[tuple[bytes, bytes]]) -> tuple[int, dict, bool]:
    """POST straight into the ASGI app, no auth, and report whether the body
    was ever read."""
    from backend.main import app

    read = False
    sent: dict = {}

    async def receive():
        nonlocal read
        read = True
        return {"type": "http.request", "body": b"x" * 1024, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] = sent.get("body", b"") + message.get("body", b"")

    path = f"/api/v1/reservations/locations/{LOCATION_ID}/floor/ai-drafts"
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "server": ("test", 80), "client": ("1.2.3.4", 1),
        "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
        "headers": [(b"content-type", b"multipart/form-data; boundary=x"), *headers],
    }
    await app(scope, receive, send)
    return sent["status"], json.loads(sent["body"]), read


@pytest.mark.asyncio
async def test_an_oversized_body_is_refused_before_it_is_read():
    status, body, read = await _raw_post(
        [(b"content-length", str(floor_ai.MAX_TOTAL_BYTES + 2 * 1024 * 1024).encode())])
    assert status == 413 and body["detail"]["code"] == "file_too_large"
    assert read is False


@pytest.mark.asyncio
async def test_a_body_without_a_length_is_refused():
    status, body, read = await _raw_post([(b"transfer-encoding", b"chunked")])
    assert status == 411 and body["detail"]["code"] == "length_required"
    assert read is False


def test_suffixed_labels_fit_the_label_limit():
    long = "Window booth by bar"  # 19 characters
    floor, _ = floor_ai.postprocess(_raw([_table(long), _table(long), _table(long)]))
    labels = [t.label for t in floor.tables]
    assert len(set(labels)) == 3
    assert all(len(lbl) <= floor_ai.MAX_LABEL_CHARS for lbl in labels)


# --- reading uploads ----------------------------------------------------------


class FakeUpload:
    """An UploadFile stand-in that records how much was read."""

    def __init__(self, data: bytes, size: int | None = None) -> None:
        self.data, self.size, self.pos, self.read_sizes = data, size, 0, []

    async def read(self, n: int = -1) -> bytes:
        self.read_sizes.append(n)
        end = len(self.data) if n < 0 else self.pos + n
        chunk, self.pos = self.data[self.pos:end], min(end, len(self.data))
        return chunk

    async def seek(self, offset: int) -> None:
        self.pos = offset


@pytest.mark.asyncio
async def test_an_oversized_file_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr(floor_ai, "MAX_IMAGE_BYTES", 100)
    big = FakeUpload(JPEG + b"\x00" * 200, size=268)
    with pytest.raises(floor_ai.DraftInputInvalid) as e:
        await floor_ai.read_uploads([big], [], "")
    assert e.value.code == "file_too_large" and e.value.status == 413
    # Only the few bytes that identify the type were read.
    assert -1 not in big.read_sizes


@pytest.mark.asyncio
async def test_the_total_is_checked_before_reading_the_next_file(monkeypatch):
    monkeypatch.setattr(floor_ai, "MAX_TOTAL_BYTES", 100)
    first = FakeUpload(JPEG, size=len(JPEG))
    second = FakeUpload(JPEG, size=len(JPEG))
    with pytest.raises(floor_ai.DraftInputInvalid) as e:
        await floor_ai.read_uploads([first, second], [], "")
    assert e.value.code == "file_too_large"
    assert -1 not in second.read_sizes


@pytest.mark.asyncio
async def test_bytes_read_count_when_the_size_is_not_declared(monkeypatch):
    monkeypatch.setattr(floor_ai, "MAX_TOTAL_BYTES", 100)
    first = FakeUpload(JPEG)
    second = FakeUpload(JPEG)
    with pytest.raises(floor_ai.DraftInputInvalid) as e:
        await floor_ai.read_uploads([first, second], [], "")
    assert e.value.code == "file_too_large"


@pytest.mark.asyncio
async def test_uploads_become_draft_files_with_their_captions():
    files = await floor_ai.read_uploads(
        [FakeUpload(PNG, size=len(PNG)), FakeUpload(PDF, size=len(PDF))], ["Patio"], "n")
    assert [(f.media_type, f.caption) for f in files] == [
        ("image/png", "Patio"), ("application/pdf", "")]
    assert files[0].data == PNG


def test_the_prompt_bounds_the_output():
    """The API rejects maxItems/maxLength in a JSON-output schema, so the
    bounds are stated in the prompt and enforced in postprocess."""
    assert "at most 10 warnings" in floor_ai.SYSTEM_PROMPT
    floor, warnings = floor_ai.postprocess({
        "sections": [{"key": f"s{i}", "name": "x" * 500} for i in range(80)],
        "tables": [], "combinations": [],
        "warnings": ["w" * 5000] * 100, "confidence": "low"})
    assert len(warnings) <= floor_ai.MAX_MODEL_WARNINGS + 1
    assert all(len(w) <= floor_ai.MAX_WARNING_CHARS for w in warnings)
    assert len(floor.sections) <= floor_ai.MAX_SECTIONS
    assert all(len(s.name) <= floor_ai.MAX_NAME_CHARS for s in floor.sections)


# --- input limits -----------------------------------------------------------


@pytest.mark.asyncio
async def test_file_count_and_type(client, db_session, manager_token, seed_location, model):
    await _credits(db_session, 10.0)
    h = manager_token
    r = await _post(client, h, [], notes="no files")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "no_files"

    r = await _post(client, h, _files(*[(f"{i}.jpg", JPEG, "image/jpeg") for i in range(11)]))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_files"

    r = await _post(client, h, _files(("a.gif", b"GIF89a" + b"\x00" * 10, "image/gif")))
    assert r.status_code == 415 and r.json()["detail"]["code"] == "unsupported_file"
    # The bytes decide, not the declared type.
    r = await _post(client, h, _files(("a.jpg", b"hello world", "image/jpeg")))
    assert r.status_code == 415
    assert model.calls == []


@pytest.mark.asyncio
async def test_size_limits(client, db_session, manager_token, seed_location, model, monkeypatch):
    await _credits(db_session, 10.0)
    h = manager_token
    monkeypatch.setattr(floor_ai, "MAX_IMAGE_BYTES", 100)
    r = await _post(client, h, _files(("a.jpg", JPEG + b"\x00" * 100, "image/jpeg")))
    assert r.status_code == 413 and r.json()["detail"]["code"] == "file_too_large"
    # A PDF has its own, larger limit.
    r = await _post(client, h, _files(("p.pdf", PDF + b"\x00" * 100, "application/pdf")))
    assert r.status_code == 202, r.text

    monkeypatch.setattr(floor_ai, "MAX_TOTAL_BYTES", 130)
    r = await _post(client, h, _files(("a.jpg", JPEG, "image/jpeg"), ("b.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 413 and r.json()["detail"]["code"] == "file_too_large"


@pytest.mark.asyncio
async def test_caption_and_notes_limits(client, db_session, manager_token, seed_location, model):
    await _credits(db_session, 10.0)
    h = manager_token
    one = _files(("a.jpg", JPEG, "image/jpeg"))
    r = await _post(client, h, one, captions=["x" * 301])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "caption_too_long"
    r = await _post(client, h, one, notes="x" * 2001)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "notes_too_long"
    r = await _post(client, h, one, captions=["a", "b"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_captions"
    assert model.calls == []


@pytest.mark.asyncio
async def test_404_when_flag_off(client, manager_token, seed_location, monkeypatch, model):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    r = await _post(client, manager_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 404
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/floor/ai-drafts/abcd1234",
                         headers=_auth(manager_token))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_employees_cannot_draft(client, employee_token, seed_location, model):
    r = await _post(client, employee_token, _files(("a.jpg", JPEG, "image/jpeg")))
    assert r.status_code == 403
    assert model.calls == []


# --- polling scope and timeout ----------------------------------------------


async def _row(db: AsyncSession, *, company=COMPANY_ID, location=LOCATION_ID,
               status="queued", age=timedelta(0)) -> FloorPlanDraft:
    row = FloorPlanDraft(company_id=company, location_id=location, status=status,
                         warnings=[], created_at=datetime.now(timezone.utc) - age)
    db.add(row)
    await db.commit()
    return row


@pytest.mark.asyncio
async def test_polling_is_company_and_location_scoped(
    client, db_session, manager_token, seed_location, seed_region
):
    from backend.models import Company as Co, Location, Region

    other_loc = Location(company_id=COMPANY_ID, region_id=seed_region.id, name="Other",
                         timezone="America/New_York")
    db_session.add(other_loc)
    db_session.add(Co(id=COMPANY2_ID, name="Other co", slug="other-co-floor"))
    await db_session.flush()
    region2 = Region(company_id=COMPANY2_ID, name="R2")
    db_session.add(region2)
    await db_session.flush()
    loc2 = Location(company_id=COMPANY2_ID, region_id=region2.id, name="Theirs",
                    timezone="America/New_York")
    db_session.add(loc2)
    await db_session.commit()

    mine = await _row(db_session)
    elsewhere = await _row(db_session, location=other_loc.id)
    theirs = await _row(db_session, company=COMPANY2_ID, location=loc2.id)
    h = _auth(manager_token)
    url = f"{BASE}/locations/{LOCATION_ID}/floor/ai-drafts"
    assert (await client.get(f"{url}/{mine.id}", headers=h)).status_code == 200
    assert (await client.get(f"{url}/{elsewhere.id}", headers=h)).status_code == 404
    assert (await client.get(f"{url}/{theirs.id}", headers=h)).status_code == 404
    r = await client.get(f"{BASE}/locations/{loc2.id}/floor/ai-drafts/{theirs.id}", headers=h)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a_stuck_draft_reads_as_timed_out(client, db_session, manager_token, seed_location):
    stuck = await _row(db_session, status="running", age=timedelta(minutes=6))
    fresh = await _row(db_session, status="running", age=timedelta(minutes=1))
    h = _auth(manager_token)
    url = f"{BASE}/locations/{LOCATION_ID}/floor/ai-drafts"
    body = (await client.get(f"{url}/{stuck.id}", headers=h)).json()
    assert body["status"] == "failed" and body["error_code"] == "timeout"
    body = (await client.get(f"{url}/{fresh.id}", headers=h)).json()
    assert body["status"] == "running" and body["error_code"] is None


@pytest.mark.asyncio
async def test_a_late_result_after_timeout_is_not_charged(db_session, seed_location, model):
    """The job finishing after the poller gave up must not charge for a
    draft nobody will see."""
    await _credits(db_session, 10.0)
    row = await _row(db_session, status="queued", age=timedelta(minutes=6))
    files = floor_ai.validate_inputs([(JPEG, "")], "")
    await floor_ai.run_draft(row.id, files, "", COUNTED)
    fresh = (await db_session.execute(select(FloorPlanDraft).where(
        FloorPlanDraft.id == row.id).execution_options(populate_existing=True))).scalar_one()
    assert fresh.status == "failed" and fresh.error_code == "timeout"
    assert (await _og(db_session)).ai_credits_usd == pytest.approx(10.0)
    daily = (await db_session.execute(select(TokenUsageDaily))).scalar_one()
    assert daily.cost_usd == pytest.approx(COST)


# --- retention --------------------------------------------------------------


@pytest.mark.asyncio
async def test_drafts_are_deleted_after_seven_days(db_session, seed_location):
    old = await _row(db_session, status="done", age=timedelta(days=8))
    recent = await _row(db_session, status="done", age=timedelta(days=6))
    summary = await run_data_retention(db_session)
    await db_session.commit()
    ids = (await db_session.execute(select(FloorPlanDraft.id))).scalars().all()
    assert ids == [recent.id] and old.id not in ids
    assert summary["old_floor_plan_drafts_deleted"] == 1


# --- post-processing --------------------------------------------------------


def _table(label: str, **kw) -> dict:
    return {"label": label, "section_key": None, "min_seats": 1, "max_seats": 2,
            "combinable": False, "x": 1, "y": 1, "w": 2, "h": 2, "rotation": 0, **kw}


def _raw(tables, combinations=(), sections=()) -> dict:
    return {"sections": list(sections), "tables": list(tables),
            "combinations": list(combinations), "warnings": [], "confidence": "low"}


def test_duplicate_labels_get_a_suffix():
    floor, warnings = floor_ai.postprocess(_raw([_table("T1"), _table("T1"), _table("T1")]))
    assert [t.label for t in floor.tables] == ["T1", "T1 (2)", "T1 (3)"]
    assert any("T1" in w for w in warnings)
    validate_floor(floor)


def test_tables_are_snapped_and_kept_on_the_canvas():
    floor, _ = floor_ai.postprocess(_raw([
        _table("A", x=50.2, y=-3, w=2.26, h=0.1),
        _table("B", x=3.74, y=24.9, w=80, h=2),
    ]))
    a, b = floor.tables
    assert (a.x, a.y, a.w, a.h) == (37.5, 0.0, 2.5, 0.5)
    # Too wide for the canvas: the whole width, from the start.
    assert (b.x, b.w) == (0.0, 40.0) and b.y + b.h <= 25


def test_seats_are_ordered_and_bounded():
    floor, _ = floor_ai.postprocess(_raw([
        _table("A", min_seats=6, max_seats=4),
        _table("B", min_seats=0, max_seats=0),
        _table("C", min_seats=2, max_seats=99),
    ]))
    assert [(t.min_seats, t.max_seats) for t in floor.tables] == [(4, 6), (1, 1), (2, 30)]


def test_bad_combinations_and_sections_are_dropped_with_a_warning():
    floor, warnings = floor_ai.postprocess(_raw(
        [_table("T1", combinable=True, section_key="patio"),
         _table("T2", combinable=False), _table("T3", combinable=True)],
        combinations=[
            {"name": "ok", "table_labels": ["T1", "T3"]},
            {"name": "not combinable", "table_labels": ["T1", "T2"]},
            {"name": "unknown", "table_labels": ["T1", "T9"]},
            {"name": "alone", "table_labels": ["T1", "T1"]},
        ],
        sections=[{"key": "main", "name": "Main"}, {"key": "main", "name": "Dup"}],
    ))
    assert [c.name for c in floor.combinations] == ["ok"]
    assert [s.key for s in floor.sections] == ["main"]
    assert floor.tables[0].section_key is None
    joined = " ".join(warnings)
    for name in ("not combinable", "unknown", "alone", "patio"):
        assert name in joined
    validate_floor(floor)


def test_at_most_200_tables():
    floor, warnings = floor_ai.postprocess(_raw([_table(f"T{i}") for i in range(250)]))
    assert len(floor.tables) == 200
    assert any("200" in w for w in warnings)


def test_blank_labels_are_named():
    floor, _ = floor_ai.postprocess(_raw([_table("  "), _table("T1")]))
    labels = [t.label for t in floor.tables]
    assert "" not in labels and len(set(labels)) == 2


def test_cost_uses_the_floor_plan_prices():
    assert floor_ai.draft_cost_usd(dict(USAGE)) == pytest.approx(COST)
    assert floor_ai.draft_cost_usd({
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 1_000_000, "cache_write_tokens": 1_000_000,
    }) == pytest.approx(settings.FLOOR_PLAN_AI_CACHE_READ_USD_PER_MTOK
                        + settings.FLOOR_PLAN_AI_CACHE_WRITE_USD_PER_MTOK)
