"""Redaction pipeline (T-22, ARCHITECTURE.md §8.3).

Covers the pure pipeline, the LLM boundary (no raw PII/secret reaches the model),
and persisted step payloads (no unredacted PII in ``investigation_steps``).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taproot.core.events import InMemoryEventBus
from taproot.core.models import LLMMessage
from taproot.core.redaction import (
    hash_user,
    redact_messages,
    redact_text,
    redact_value,
)
from taproot.db.models import Investigation, InvestigationStep, Project, StepStatus
from taproot.integrations.llm import FakeLLM, RedactingLLM
from taproot.workers.steps import StepRecorder


# --- pure pipeline ----------------------------------------------------------
def test_hash_user_is_stable_and_countable() -> None:
    assert hash_user("alice") == hash_user("alice")
    assert hash_user("alice") != hash_user("bob")
    assert hash_user("alice").startswith("user_")  # type: ignore[union-attr]
    assert hash_user(None) is None
    assert hash_user("") is None


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("contact alice@example.com now", "alice@example.com"),
        ("card 4111 1111 1111 1111 charged", "4111 1111 1111 1111"),
        ("ssn 123-45-6789 on file", "123-45-6789"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("token glpat-ABCDEFGHIJKLMNOPQRST", "glpat-ABCDEFGHIJKLMNOPQRST"),
    ],
)
def test_redact_text_masks_pii_and_secrets(raw: str, leaked: str) -> None:
    out = redact_text(raw)
    assert leaked not in out


def test_redact_text_masks_email_but_keeps_domain() -> None:
    assert redact_text("from alice@example.com") == "from a***@example.com"


def test_redact_text_leaves_ordinary_text() -> None:
    assert redact_text("NullPointerException in PaymentService") == (
        "NullPointerException in PaymentService"
    )


def test_redact_value_hashes_user_keys_and_recurses() -> None:
    payload = {
        "user_name": "alice",
        "nested": {"note": "email bob@corp.io", "count": 3},
        "items": ["Bearer sk-topsecrettoken", "clean"],
    }
    out = redact_value(payload)
    assert out["user_name"] == hash_user("alice")
    assert "bob@corp.io" not in out["nested"]["note"]
    assert out["nested"]["count"] == 3  # non-strings pass through
    assert "topsecrettoken" not in out["items"][0]
    assert out["items"][1] == "clean"


# --- LLM boundary -----------------------------------------------------------
async def test_redacting_llm_never_forwards_raw_pii_on_complete() -> None:
    inner = FakeLLM(responses=['{"ok": true}'])
    llm = RedactingLLM(inner)
    await llm.complete(
        [
            LLMMessage(role="system", content="You are a helper."),
            LLMMessage(
                role="user",
                content="user alice@example.com hit it with Bearer glpat-ABCDEFGHIJKLMNOPQRST",
            ),
        ]
    )
    forwarded = " ".join(m.content for m in inner.received[0])
    assert "alice@example.com" not in forwarded
    assert "glpat-ABCDEFGHIJKLMNOPQRST" not in forwarded
    assert "REDACTED" in forwarded or "a***@example.com" in forwarded


async def test_redacting_llm_redacts_stream() -> None:
    inner = FakeLLM(responses=["ok"])
    llm = RedactingLLM(inner)
    async for _ in llm.stream([LLMMessage(role="user", content="ssn 123-45-6789")]):
        pass
    forwarded = " ".join(m.content for m in inner.received[0])
    assert "123-45-6789" not in forwarded


# --- persisted step payloads ------------------------------------------------
async def test_step_payload_is_redacted_before_persist(session: AsyncSession) -> None:
    project = Project(name="P", slug=f"p-{id(session)}")
    session.add(project)
    await session.flush()
    inv = Investigation(project_id=project.id, error_text="x")
    session.add(inv)
    await session.commit()

    recorder = StepRecorder(session, InMemoryEventBus(), inv.id)
    seq = await recorder.start("thread_walk", "Walking")
    await recorder.finish(
        seq,
        "thread_walk",
        status=StepStatus.ok,
        summary="user carol@corp.io saw glpat-ABCDEFGHIJKLMNOPQRST",
        payload={"user_name": "carol", "line": "email dave@corp.io"},
    )

    step = (
        await session.execute(
            select(InvestigationStep).where(InvestigationStep.investigation_id == inv.id)
        )
    ).scalar_one()
    assert step.summary is not None
    assert "carol@corp.io" not in step.summary
    assert "glpat-ABCDEFGHIJKLMNOPQRST" not in step.summary
    assert step.payload is not None
    assert step.payload["user_name"] == hash_user("carol")
    assert "dave@corp.io" not in step.payload["line"]


def test_redact_messages_preserves_roles() -> None:
    msgs = [LLMMessage(role="system", content="hi"), LLMMessage(role="user", content="alice@x.io")]
    out = redact_messages(msgs)
    assert [m.role for m in out] == ["system", "user"]
    assert "alice@x.io" not in out[1].content
