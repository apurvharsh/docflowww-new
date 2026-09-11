"""Scanner Agent — efficient semantic review with deterministic scoring."""

from __future__ import annotations

from agno.agent import Agent
from agno.models.google import 

from app.config import settings
from app.services.document_workflow import extract_document_context, reform_document, score_document
from app.tools.document_tools import reform_document_tool, score_document_tool


def create_scanner_agent() -> Agent:
    """Create the configured scanner agent without exposing credentials."""
    return Agent(
        name="Scanner Agent",
        role=(
            "You are a document quality scanner. Evaluate documents using the "
            "provided scoring tool, never invent scores or criterion results, and "
            "request a reform only when the actual score is below the threshold."
        ),
        model=(
            id=settings._generation_model,
            api_key=settings._api_key,
        ),
        tools=[score_document_tool, reform_document_tool],
        instructions=(
            "You are the Scanner Agent for DocFlow.\n\n"
            "Core rules:\n"
            "1. Never calculate, estimate, guess, or invent a score.\n"
            "2. Always call score_document_tool with the complete document content, "
            "document type, and project stage before reporting a score.\n"
            "3. The score_document_tool result is the only authority for the overall "
            "score, criterion scores, criterion notes, summary, and pass/fail status.\n"
            "4. Do not convert the 0-60 score to another scale.\n"
            "5. The scoring tool may use semantic evidence for synonyms, alternative "
            "headings, context, and references, but Python remains authoritative for "
            "the final score and threshold.\n\n"
            "Workflow:\n"
            "- If the actual score is 36 or higher, return the actual breakdown and "
            "summary, do not call reform_document_tool, and say that explicit human "
            "confirmation is required before saving.\n"
            "- If the actual score is below 36, call reform_document_tool with the "
            "original document and actual scan result. Present the returned text only "
            "as a suggested revision pending human review.\n"
            "- Never save or claim to save a document automatically.\n"
            "- Do not call score_document_tool twice for the same unchanged document."
        ),
    )


def _tool_score_result(tools: list) -> dict | None:
    """Extract the scoring tool's dict result across Agno tool result shapes."""
    for tool_result in tools:
        candidate = getattr(tool_result, "result", tool_result)
        if isinstance(candidate, dict) and {"total", "criteria", "passed"} <= candidate.keys():
            return candidate
    return None


def run_scanning_agent(message: str, state: dict | None = None) -> dict:
    state = state or {}
    document_text = state.get("current_content") or message
    info = extract_document_context(message)
    document_type = state.get("document_type") or info.get("document_type") or "General Document"
    project_stage = state.get("project_stage") or info.get("project_stage") or "Requirements"
    context = (
        "Scan the following document using the required rubric. "
        "Use score_document_tool before making any scoring claim.\n\n"
        f"Document Type:\n{document_type}\n\n"
        f"Project Stage:\n{project_stage}\n\n"
        f"Document Content:\n{document_text}"
    )

    try:
        run_output = create_scanner_agent().run(context)
        tools = getattr(run_output, "tools", []) or []
        score = _tool_score_result(tools)
        if score is None:
            raise RuntimeError("Scanner Agent did not return a valid score_document_tool result")
        result = {
            "response": getattr(run_output, "content", str(run_output)),
            "tools": tools,
            "state": state,
            "document_type": document_type,
            "project_stage": project_stage,
            "score": score,
        }
    except Exception as exc:
        score = score_document(document_text, document_type, project_stage)
        result = {
            "response": (
                "Scanner Agent provider call failed; the local scoring tool result "
                "was used instead. Retry to use the configured  model.\n"
                f"Provider error: {exc}"
            ),
            "tools": [],
            "state": state,
            "document_type": document_type,
            "project_stage": project_stage,
            "score": score,
            "error": str(exc),
        }

    if not score.get("passed"):
        revised = reform_document(document_text, document_type, project_stage, score)
        result["revised_document"] = revised
        result["response"] = (
            f"{result['response']}\n\n"
            f"Scan result: {score['total']}/60, below the 36/60 threshold.\n"
            "Suggested revision (pending human review, not final):\n\n"
            f"{revised}"
        )
    return result
