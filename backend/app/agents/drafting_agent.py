"""Drafting Agent — command-driven document drafting using Agno + Groq."""

from __future__ import annotations

import os

from agno.agent import Agent
from agno.models.groq import Groq

from app.services.document_workflow import extract_document_context, generate_draft
from app.tools.document_tools import extract_document_context_tool, generate_draft_tool


class DraftingAgent:
    """Backward-compatible wrapper for the drafting workflow."""

    @staticmethod
    def generate_document_outline(project_id: str, stage: str, existing_docs: list[dict], user) -> str:
        return generate_draft("General Document", stage, user_prompt=f"Outline for {project_id}: {stage}")

    @staticmethod
    def suggest_content(document_type: str, project_context: str, stage: str) -> str:
        return generate_draft(document_type, stage, user_prompt=project_context)


def create_drafting_agent() -> Agent:
    api_key = os.getenv("GROQ_API_KEY")
    model = Groq(id="openai/gpt-oss-120b", api_key=api_key or "demo-key")
    return Agent(
        name="Drafting Agent",
        model=model,
        tools=[extract_document_context_tool, generate_draft_tool],
        instructions=(
            "You are the Drafting Agent. Follow this deterministic workflow:\n"
            "1. Extract the document type and project stage from the user's message if possible.\n"
            "2. If either is missing, ask for it before drafting.\n"
            "3. If the user gives a template preference, use it.\n"
            "4. Use the generate_draft tool to create the draft; never invent a draft without calling the tool.\n"
            "5. For edits, use the full current content and the user's change request to redraft.\n"
            "6. The drafting service is not the project subject. Never put the service or "
            "provider name in the generated document title or body; write only about the "
            "project described by the user.\n"
            "7. When the user explicitly approves, mark the draft ready for scanning."
        ),
    )


def run_drafting_agent(message: str, state: dict | None = None) -> dict:
    state = state or {}
    result = {
        "response": None,
        "tools": [],
        "state": state,
    }
    try:
        agent = create_drafting_agent()
        context = (
            "Current context summary:\n"
            f"- document_type: {state.get('document_type') or 'unknown'}\n"
            f"- project_stage: {state.get('project_stage') or 'unknown'}\n"
            f"- draft_status: {state.get('draft_status') or 'new'}\n"
            f"- current_content: {state.get('current_content') or ''}\n\n"
            f"User message: {message}"
        )
        run_output = agent.run(context)
        result["response"] = getattr(run_output, "content", str(run_output))
        result["tools"] = getattr(run_output, "tools", []) or []
    except Exception:
        info = extract_document_context(message)
        document_type = state.get("document_type") or info["document_type"]
        project_stage = state.get("project_stage") or info["project_stage"]
        draft = generate_draft(
            document_type,
            project_stage,
            user_prompt=message,
            template=info.get("template"),
            current_content=state.get("current_content"),
        )
        result["response"] = draft
        result["tools"] = [
            {"name": "generate_draft", "result": draft},
            {"name": "extract_document_context", "result": info},
        ]
    return result
