"""Structure proposal (P0.2) — from a photo or a sentence to a proposed tree.

Three pieces, none of which persists anything:

- :func:`build_structure_context` — ground the prompt + feed the validator.
- :func:`validate_proposal` — the pure Step 4 that trims the model's answer and
  reports every change it made as a warning.
- :func:`build_template_proposal` — the no-LLM starter structure.

The orchestration (one LLM call with retries, one ``AgentTrace``) lives in
:mod:`app.services.structure_proposal_service`.
"""
from app.agents.structure.context import StructureContext, build_structure_context
from app.agents.structure.template import build_template_proposal
from app.agents.structure.validate import ProposalWarning, validate_proposal

__all__ = [
    "ProposalWarning",
    "StructureContext",
    "build_structure_context",
    "build_template_proposal",
    "validate_proposal",
]
