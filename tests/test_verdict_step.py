"""The verdict step (ADR 0087): written once over the frozen draft, never load-bearing.

The whole-run half — the step runs once, spends once, and stores a sentence with a valid
tone — is asserted where the whole run is driven, in ``test_workflow.py``. What lives here
is the two properties that make the authored half safe to have at all: a model outage costs
the run nothing but the sentence, and the schema has no field through which the sentence
could ever become evidence.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.verdict import AuthoredTone, AuthoredVerdict, VerdictAgent, VerdictInput
from aer.errors import AerError
from aer.workflow.engine import StepContext
from aer.workflow.workflows import vertical_slice_v1
from tests.workflow_fixtures import make_provider, seed_job, seed_request, seed_user

pytestmark = pytest.mark.integration


class TestTheSchemaCannotCarryEvidence:
    def test_the_output_is_a_sentence_and_a_tone_and_nothing_else(self) -> None:
        """The structural enforcement ADR 0087 asks for begins at the schema.

        A field added here — a source, an excerpt, a figure — is the laundering route the
        record forbids, and this assertion makes adding one a decision somebody has to
        record rather than a diff.
        """
        assert set(AuthoredVerdict.model_fields) == {"sentence", "tone"}

    def test_the_tone_vocabulary_is_closed_and_never_claims_a_fault(self) -> None:
        """Refusal and failure are the platform's claims about itself; an interpretation
        reaching for either would be asserting a fault rather than reading a record."""
        assert {tone.value for tone in AuthoredTone} == {"success", "warning", "info"}

    def test_the_input_carries_no_evidence_shaped_field(self) -> None:
        names = set(VerdictInput.model_fields)
        forbidden = {"claims", "citations", "sources", "evidence", "calculations", "facts"}
        assert not names & forbidden, (
            "VerdictInput grew an evidence-shaped field. The interpreter is handed the "
            "shape of the record, never the record, precisely so its output can never be "
            "mistaken for a reading of evidence."
        )


class TestAnOutageCostsOnlyTheSentence:
    async def test_a_model_failure_degrades_to_written_false(
        self,
        db_session: AsyncSession,
        workflow_settings: Any,
        workflow_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The page falls back to its composed half, which is a complete sentence on its
        own — so every failure short of a budget refusal is absorbed, logged and recorded
        as ``written: False`` rather than costing the run its gate."""
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        job = await seed_job(db_session, request=request)

        async def refuse(self: VerdictAgent, *args: Any, **kwargs: Any) -> AuthoredVerdict:
            message = "the provider is down"
            raise AerError(message)

        monkeypatch.setattr(VerdictAgent, "run", refuse)

        # The step row the engine would hand the function. Only its identity is read on
        # this path: the agent refuses before anything else needs to be real.
        step_row = vertical_slice_v1.JobStep(job_id=job.id, step_key="verdict", sequence=1)
        context = StepContext(
            session=db_session,
            job=job,
            step=step_row,
            services={
                "provider": make_provider(),
                "router": None,
                "settings": workflow_settings,
                "store": workflow_store,
            },
        )

        result = await vertical_slice_v1._verdict(context)

        assert result.output == {"written": False}
