"""API-level AI validation auth / blindness schema tests (no live providers)."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from services.api.app.ai_validation_routes import AIValidationQueueItem, AIValidationResultCreate


def test_queue_item_forbids_score_field():
    with pytest.raises(ValidationError):
        AIValidationQueueItem(
            message_id=1,
            sample_batch_id="b",
            text="hi",
            task_token="t",
            score=99,  # type: ignore[call-arg]
        )


def test_queue_item_minimal_ok():
    item = AIValidationQueueItem(message_id=1, sample_batch_id="b", text="hi", task_token="tok")
    assert item.text == "hi"


def test_result_create_forbids_extra():
    with pytest.raises(ValidationError):
        AIValidationResultCreate(
            message_id=1,
            sample_batch_id="b",
            reviewer_id="aival_a_x",
            validation_run_id="r",
            task_token="t",
            attempt_no=1,
            status="ok",
            scorer_tier="HIGH",  # type: ignore[call-arg]
        )
