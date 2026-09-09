"""Service-layer workflow for drafting, scanning, and saving project documents."""

from __future__ import annotations

import json
import html
import os
import re
from pathlib import Path
from typing import Any

from app.retrieval.embeddings import generate_gemini_text

BASE_DIR = Path(__file__).resolve().parents[1]
DRAFTS_DIR = BASE_DIR.parent / "drafts"
DRAFTS_DIR.mkdir(exist_ok=True, parents=True)

DOCUMENT_TYPE_ALIASES = {
    "PRD": ["prd", "product requirements document", "product requirement doc"],
    "BRD": ["brd", "business requirements document"],
    "SRS": ["srs", "software requirements specification"],
    "ADR": ["adr", "architecture decision record"],
    "API Specification": ["api specification", "api spec", "openapi", "rest api"],
    "Project Plan": ["project plan", "implementation plan"],
    "Design Document": ["design document", "technical design"],
    "Risk Register": ["risk register", "risks"],
    "Test Plan": ["test plan"],
    "Runbook": ["runbook", "operations runbook"],
    "Release Notes": ["release notes"],
    "Proposal": ["proposal", "statement of work"],
    "Meeting Notes": ["meeting notes", "meeting summary"],
    "Other": ["other document", "general doc", "document"],
}

STAGE_ALIASES = {
    "Intake": ["intake"],
    "Discovery": ["discovery", "research"],
    "Requirements": ["requirements", "requirement"],
    "Planning": ["planning", "plan"],
    "Architecture": ["architecture", "architectural"],
    "Design": ["design"],
    "Development": ["development", "build"],
    "Integration": ["integration"],
    "Quality Assurance": ["quality assurance", "qa", "testing"],
    "User Acceptance Testing": ["uat", "user acceptance testing"],
    "Release": ["release"],
    "Operations": ["operations", "ops"],
    "Maintenance": ["maintenance"],
    "Retirement": ["retirement"],
}


def _match_aliases(message: str, aliases: dict[str, list[str]]) -> str | None:
    lowered = message.lower()
    for label, patterns in aliases.items():
        for pattern in patterns:
            if pattern in lowered:
                return label
    return None


def extract_document_context(message: str) -> dict[str, Any]:
    text = (message or "").strip()
    stage = _match_aliases(text, STAGE_ALIASES)
    doc_type = _match_aliases(text, DOCUMENT_TYPE_ALIASES)

    if not doc_type:
        for pattern, label in (
            (r"\b(\w+\s+requirements? document)\b", "PRD"),
            (r"\b(\w+\s+requirements? specification)\b", "SRS"),
            (r"\b(architecture decision record)\b", "ADR"),
            (r"\b(api specification|api spec)\b", "API Specification"),
            (r"\b(test plan|test case)\b", "Test Plan"),
        ):
            if re.search(pattern, text, flags=re.IGNORECASE):
                doc_type = label
                break

    if not stage:
        stage = "Unspecified"
    if not doc_type:
        doc_type = "General Document"

    return {
        "document_type": doc_type,
        "project_stage": stage,
        "template": _infer_template(text, doc_type, stage),
        "raw_message": text,
    }


def _infer_template(message: str, doc_type: str, stage: str) -> str | None:
    lowered = message.lower()
    for token in ("template", "follow", "use"):
        if token in lowered:
            for part in re.split(r"\btemplate\b|\bfollow\b|\buse\b", message, flags=re.IGNORECASE):
                if part.strip():
                    return part.strip()
    return None


_SEMANTIC_RUBRIC_LEVELS = {"strong", "partial", "missing"}


def _parse_semantic_rubric_review(raw: str | None) -> dict[str, dict[str, str]] | None:
    """Validate the LLM's rubric classification without allowing it to score."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None

    review: dict[str, dict[str, str]] = {}
    for criterion in ("structural_clarity", "completeness", "labeling_accuracy"):
        item = parsed.get(criterion)
        if not isinstance(item, dict):
            return None
        level = item.get("level")
        evidence = item.get("evidence")
        missing = item.get("missing")
        if (
            not isinstance(level, str)
            or level.lower() not in _SEMANTIC_RUBRIC_LEVELS
            or not isinstance(evidence, str)
            or not isinstance(missing, str)
        ):
            return None
        review[criterion] = {
            "level": level.lower(),
            "evidence": evidence.strip(),
            "missing": missing.strip(),
        }
    return review


def _semantic_rubric_review(document_text: str, document_type: str, project_stage: str) -> dict[str, dict[str, str]] | None:
    """Ask the LLM for semantic rubric evidence, never for points or pass/fail."""
    prompt = (
        "You are a strict document-rubric reviewer. Evaluate whether the document "
        "satisfies the intent of each rubric criterion, not whether it uses exact "
        "words. Treat synonyms, alternative headings, references to another section, "
        "and minor spelling mistakes as valid when the meaning and context are clear. "
        "Do not invent content that is not present. Return ONLY valid JSON with exactly "
        "these keys: structural_clarity, completeness, labeling_accuracy. Each value "
        "must contain exactly level, evidence, and missing. The level must be one of "
        '"strong", "partial", or "missing". Do not return scores, percentages, '
        "thresholds, or pass/fail decisions.\n\n"
        "Criterion meanings:\n"
        "- structural_clarity: sections are organized and relationships between "
        "sections make the document understandable.\n"
        "- completeness: the document covers the meaningful requirements expected "
        "for its type and SDLC stage, even if different headings or terminology are used.\n"
        "- labeling_accuracy: the title and headings accurately communicate the "
        "document's purpose and stage; semantic equivalents such as Goals, Objective, "
        "or Project Purpose count when they express the requested concept.\n\n"
        f"Document type: {document_type}\n"
        f"Project stage: {project_stage}\n"
        f"DOCUMENT:\n{document_text}"
    )
    return _parse_semantic_rubric_review(generate_gemini_text(prompt))


def _semantic_points(base_score: int, level: str, maximum: int) -> int:
    """Map semantic evidence to points deterministically in the existing rubric."""
    if level == "strong":
        return maximum
    if level == "partial":
        return max(0, min(maximum, max(base_score, 12)))
    return max(0, min(base_score, 10))


def build_context_summary(context: dict[str, Any]) -> str:
    doc_type = context.get("document_type") or "General Document"
    stage = context.get("project_stage") or "Unspecified"
    status = context.get("draft_status") or "new"
    content_len = len((context.get("current_content") or "").strip())
    return (
        "Active context:\n"
        f"- Document type: {doc_type}\n"
        f"- Project stage: {stage}\n"
        f"- Draft status: {status}\n"
        f"- Current draft length: {content_len} chars\n"
    )


def _domain_requirements(request_text: str) -> dict[str, list[str]] | None:
    lowered = request_text.lower()
    if any(token in lowered for token in ("chat", "messaging", "message")) and any(
        token in lowered for token in ("encrypt", "encrypted", "end to end", "e2ee", "secure")
    ):
        return {
            "needs": [
                "Users need private one-to-one and group conversations with clear delivery and read-status feedback.",
                "Users need confidence that only intended participants can read message content.",
                "Operators need abuse reporting and service health visibility without access to plaintext message content.",
            ],
            "requirements": [
                "The system shall establish authenticated sessions and verify the identity and device keys of conversation participants.",
                "The client shall encrypt message content end to end before transmission and decrypt it only on authorized recipient devices.",
                "The service shall not store or expose plaintext message content, encryption keys, or recoverable message history.",
                "The system shall support one-to-one and group conversations with membership changes, message ordering, retries, and offline delivery.",
                "The client shall warn users when a participant identity or device key changes and provide a way to verify safety information.",
                "The system shall protect local keys using platform-secure storage and require re-authentication for sensitive key operations.",
                "The system shall provide rate limiting, abuse reporting, account recovery, and key revocation without weakening message confidentiality.",
                "Operational logs shall use metadata minimization and shall never include message bodies, plaintext attachments, or secret keys.",
            ],
            "acceptance": [
                "A sender can encrypt a message locally, deliver it to an authorized recipient, and display the decrypted content only on that recipient's device.",
                "A network observer or service operator cannot recover plaintext message content from transport traffic, stored data, or routine logs.",
                "A removed participant cannot decrypt messages sent after removal, subject to the documented group-key rotation policy.",
                "Tampered, replayed, expired, or incorrectly signed messages are rejected and surfaced without exposing plaintext.",
                "A device-key change produces a visible warning and requires an explicit user action before the conversation is trusted again.",
                "Automated tests verify encryption, authentication, key rotation, offline delivery, failure handling, and no-plaintext logging.",
            ],
            "discovery": [
                "Define the threat model, supported devices, identity model, key lifecycle, recovery expectations, and regulatory constraints.",
                "Choose and document reviewed cryptographic protocols and libraries; do not invent cryptographic primitives.",
                "Validate usability for device changes, lost devices, multi-device sessions, group membership changes, and account recovery.",
            ],
            "open_questions": [
                "Which threat actors, metadata protections, retention rules, and compliance obligations are in scope?",
                "Which audited protocol and key-management approach will be used for one-to-one, group, and multi-device conversations?",
                "What should happen to unread messages, attachments, and group history when a device is lost or a member is removed?",
                "Which abuse-reporting data can a user voluntarily share without exposing unrelated conversation content?",
            ],
        }
    if not any(token in lowered for token in ("school", "student", "teacher", "classroom", "academic")):
        return None
    return {
        "needs": [
            "School administrators need a single source of truth for students, staff, classes, and academic records.",
            "Teachers need fast access to attendance, schedules, assignments, and student progress.",
            "Students and guardians need secure access to schedules, results, attendance, fees, and school notices.",
        ],
        "requirements": [
            "The system shall provide role-based access for administrators, teachers, students, guardians, and finance staff.",
            "The system shall maintain student profiles, enrollment history, guardian relationships, and emergency contacts.",
            "The system shall manage classes, sections, subjects, academic terms, timetables, and teacher assignments.",
            "Teachers shall be able to record daily attendance and authorized users shall be able to review attendance history and alerts.",
            "Teachers shall be able to create assignments, record grades, and publish report cards with an approval workflow.",
            "Finance staff shall be able to configure fee structures, record payments, issue receipts, and track outstanding balances.",
            "The system shall send role-appropriate announcements and notifications to students, guardians, and staff.",
            "Administrators shall be able to generate reports for enrollment, attendance, academic performance, and fees.",
            "The system shall maintain an audit trail for changes to student records, grades, attendance, payments, and permissions.",
            "The system shall protect personal data with authentication, authorization, encryption in transit, and configurable retention rules.",
        ],
        "acceptance": [
            "An administrator can create a school term, class, subject, and user account with the correct role.",
            "A teacher can record attendance and a guardian can view attendance only for their linked student.",
            "A teacher can submit grades for review and an authorized administrator can publish a report card.",
            "A finance user can record a fee payment and retrieve an accurate receipt and balance.",
            "Unauthorized users cannot access records outside their assigned school, class, or student relationship.",
            "Critical record changes are visible in the audit log with actor, timestamp, and before/after context.",
        ],
        "discovery": [
            "Primary users are school administrators, teachers, students, guardians, and finance staff.",
            "The discovery scope includes enrollment, student records, class and timetable management, attendance, grades, fees, notifications, and reporting.",
            "Research should validate current school workflows, paper or spreadsheet dependencies, approval responsibilities, and privacy obligations.",
            "Key assumptions are that each user has a defined role, students may have multiple guardians, and academic data is organized by term and class.",
            "Alternatives to evaluate include integrating with an existing student information system versus introducing a unified school management platform.",
        ],
        "open_questions": [
            "Which roles can create, edit, approve, and export student, attendance, grade, and finance records?",
            "How should the system model schools, campuses, academic years, terms, classes, sections, and guardian relationships?",
            "Which notifications are required, which channels are allowed, and how is consent managed?",
            "What retention, export, audit, and privacy requirements apply to student and financial data?",
            "Which existing systems must integrate with the school management app at launch?",
        ],
    }


def _generic_requirements(request_text: str, project_stage: str) -> dict[str, list[str]]:
    """Build useful project-specific content when no known domain blueprint applies."""
    subject = re.sub(r"^\s*(create|build|design|develop|draft|generate|make)\s+", "", request_text, flags=re.IGNORECASE).strip(" .")
    subject = subject or request_text.strip(" .")
    return {
        "needs": [
            f"Users need a reliable way to achieve the outcome requested for {subject}.",
            f"Owners need visibility into scope, decisions, dependencies, and progress for {subject}.",
            f"Stakeholders need measurable evidence that {subject} works for its intended users.",
        ],
        "requirements": [
            f"The solution shall deliver the core capabilities required by {subject}.",
            f"Users shall be able to complete the primary {subject} workflow with clear validation and useful error messages.",
            f"The system shall enforce authentication, authorization, input validation, and safe handling of user data for {subject}.",
            f"The solution shall record status, ownership, dependencies, and audit evidence for the {project_stage} stage.",
            f"The solution shall expose observable success and failure signals so the team can support {subject}.",
        ],
        "acceptance": [
            f"A representative user can complete the primary {subject} workflow from start to finish.",
            f"Invalid, unauthorized, and unavailable-service scenarios produce actionable errors without data loss.",
            f"Each requested capability has an owner, test evidence, and a measurable completion condition.",
            f"The {project_stage} deliverable is reviewable by stakeholders and traceable to the original instruction.",
        ],
        "discovery": [
            f"Identify the primary users, business owner, constraints, and alternatives for {subject}.",
            f"Validate the current workflow, data sources, dependencies, and success measures for {subject}.",
            f"Document assumptions and evidence needed before committing to the {project_stage} deliverable.",
        ],
        "open_questions": [
            f"Who owns {subject}, and which user roles need different permissions?",
            f"What inputs, outputs, integrations, and data-retention rules does {subject} require?",
            f"Which constraints, risks, and measurable success targets must be agreed before delivery?",
        ],
    }


def _draft_subject(request_text: str, document_type: str, project_stage: str) -> str:
    lowered = request_text.lower()
    if any(token in lowered for token in ("chat", "messaging", "message")) and any(
        token in lowered for token in ("encrypt", "encrypted", "end to end", "e2ee", "secure")
    ):
        return "End-to-End Encrypted Chat Interface"
    subject = re.sub(
        r"^\s*(create|build|design|develop|draft|generate|make)\s+",
        "",
        request_text,
        flags=re.IGNORECASE,
    ).strip(" .:'\"")
    subject = re.sub(r"^(a|an|the)\s+", "", subject, flags=re.IGNORECASE)
    return subject[:120] if subject else f"{document_type} — {project_stage}"


def _fallback_draft(document_type: str, project_stage: str, template: str | None = None, current_content: str | None = None) -> str:
    request_text = (template or current_content or f"Draft {document_type} for the {project_stage} stage.").strip()
    overview = f"This draft is based on the following instruction: {request_text}"
    domain = _domain_requirements(request_text) or _generic_requirements(request_text, project_stage)
    subject = _draft_subject(request_text, document_type, project_stage)
    stage_sections = {
        "Intake": ["Purpose", "Stakeholders", "Objectives", "Scope"],
        "Discovery": ["Context", "Research Summary", "Findings", "Open Questions"],
        "Requirements": ["Objective", "User Needs", "Functional Requirements", "Acceptance Criteria"],
        "Planning": ["Plan Overview", "Timeline", "Dependencies", "Risks"],
        "Architecture": ["System Overview", "Key Components", "Interfaces", "Constraints"],
        "Design": ["Design Goals", "Interaction Model", "UI / UX", "Implementation Notes"],
        "Development": ["Build Approach", "Technical Tasks", "Code Ownership", "Validation"],
        "Integration": ["Integration Points", "Contracts", "Dependencies", "Rollout Considerations"],
        "Quality Assurance": ["Test Strategy", "Test Cases", "Exit Criteria", "Defect Handling"],
        "User Acceptance Testing": ["User Scenarios", "Validation Notes", "Sign-off Checklist", "Exit Criteria"],
        "Release": ["Release Plan", "Rollout Steps", "Backward Compatibility", "Roll-back"],
        "Operations": ["Operating Model", "Monitoring", "Ownership", "Support"],
        "Maintenance": ["Maintenance Plan", "Monitoring", "Known Issues", "Update Cadence"],
        "Retirement": ["Retirement Scope", "Migration Notes", "Impact", "Closure Criteria"],
    }
    sections = stage_sections.get(project_stage, ["Overview", "Scope", "Key Details", "Status"])
    stage_guidance = {
        "Intake": [
            "Capture the business request, sponsor, constraints, stakeholders, and expected outcome.",
            "Record the decision needed to move this work into discovery.",
        ],
        "Discovery": [
            "Document research findings, user problems, assumptions, alternatives, and open questions.",
            "Define the evidence required before the solution is committed.",
        ],
        "Requirements": [
            "Define actors, functional behavior, non-functional requirements, dependencies, and measurable acceptance criteria.",
            "Trace each requirement to an owner and validation evidence.",
        ],
        "Planning": [
            "Break the request into milestones, deliverables, owners, dependencies, estimates, and delivery risks.",
            "Define the decision gates and status reporting required to control execution.",
        ],
        "Architecture": [
            "Describe the components, data flows, interfaces, deployment boundaries, security controls, and architectural decisions.",
            "Record trade-offs, constraints, failure modes, and operational quality attributes.",
        ],
        "Design": [
            "Define the user journeys, interaction states, information hierarchy, visual behavior, accessibility, and responsive requirements.",
            "Describe the design decisions needed to implement the requested experience.",
        ],
        "Development": [
            "Translate the request into implementable technical tasks, contracts, data changes, error handling, ownership, and review checkpoints.",
            "Define coding, testing, observability, and completion evidence for the implementation.",
        ],
        "Integration": [
            "Specify integration contracts, payloads, authentication, retries, failure handling, environments, and compatibility requirements.",
            "Define end-to-end validation across every connected system.",
        ],
        "Quality Assurance": [
            "Define test strategy, positive and negative scenarios, test data, automation coverage, defects, and exit criteria.",
            "Map the requested behavior to repeatable evidence that proves it works.",
        ],
        "User Acceptance Testing": [
            "Define business-user scenarios, expected outcomes, evidence, sign-off owners, and release blockers.",
            "Record the acceptance decision and unresolved limitations.",
        ],
        "Release": [
            "Define rollout sequencing, migration, feature flags, communications, monitoring, rollback, and support ownership.",
            "Specify the go-live checklist and measurable release success criteria.",
        ],
        "Operations": [
            "Define runbooks, monitoring, alerts, on-call ownership, incident response, backups, and service-level expectations.",
            "Describe how the delivered capability will be supported in production.",
        ],
        "Maintenance": [
            "Define support cadence, patching, technical debt, performance monitoring, ownership, and change controls.",
            "Set measurable criteria for keeping the capability healthy over time.",
        ],
        "Retirement": [
            "Define decommissioning scope, data retention or migration, user communication, dependencies, and rollback safeguards.",
            "Specify evidence required to close the capability safely.",
        ],
    }
    guidance = stage_guidance.get(project_stage, [
        f"Define the deliverables, owners, dependencies, risks, and validation evidence for the {project_stage} stage.",
        "Make every decision and outcome traceable to the requested input.",
    ])
    document = [
        f"# {document_type}: {subject}",
        "",
        f"## Overview",
        f"{overview}",
        "",
    ]
    for section in sections:
        document.append(f"## {section}")
        if section in {"Objective", "Purpose", "Design Goals", "Plan Overview"}:
            document.append(f"Deliver the outcome requested here: {request_text}")
            document.append(f"The objective is to produce a reviewable {document_type} for the {project_stage.lower()} phase.")
        elif section in {"User Needs", "Stakeholders", "Context", "Scope", "Overview"}:
            if domain and section == "User Needs":
                document.extend(f"- {need}" for need in domain["needs"])
            elif domain and section in {"Context", "Scope"}:
                document.extend(f"- {finding}" for finding in domain["discovery"])
            else:
                document.append(f"The document addresses the business and user needs described in this request: {request_text}")
                document.append("- Identify the primary users, owners, and stakeholders.")
                document.append("- Confirm the in-scope outcome and record exclusions before implementation.")
        elif domain and section in {"Research Summary", "Findings"}:
            document.extend(f"- {finding}" for finding in domain["discovery"])
        elif domain and section == "Open Questions":
            document.extend(f"- {question}" for question in domain["open_questions"])
        elif section in {"Functional Requirements", "Technical Tasks", "Key Components", "Integration Points", "User Scenarios"}:
            if domain and section == "Functional Requirements":
                document.extend(f"- {requirement}" for requirement in domain["requirements"])
            else:
                document.append(f"- The solution shall implement the capabilities described in: {request_text}")
                document.append("- Each capability shall have a clear owner, input, expected outcome, and acceptance condition.")
                document.append("- Requirements shall be traceable to validation evidence before sign-off.")
        elif section == "Acceptance Criteria":
            if domain:
                document.extend(f"- {criterion}" for criterion in domain["acceptance"])
            else:
                document.append(f"- The requested outcome is delivered: {request_text}")
                document.append("- Requirements are clear, testable, and measurable.")
                document.append("- Owners, dependencies, and sign-off are captured before release.")
        elif section == "Risks":
            document.append("- Risk: missing stakeholder alignment.")
            document.append("- Mitigation: define ownership and decision path.")
        else:
            document.extend(f"- {item}" for item in guidance)
            document.append(f"- Apply these controls to the requested outcome: {request_text}")
        document.append("")
    document.append("## Approval")
    document.append("Ready for review and scan before final sign-off.")
    return "\n".join(document)


def generate_draft(document_type: str, project_stage: str, user_prompt: str = "", template: str | None = None, current_content: str | None = None) -> str:
    if not document_type:
        document_type = "General Document"
    if not project_stage:
        project_stage = "Unspecified"
    text = user_prompt.strip() if user_prompt else (current_content or "")
    stage_focus = {
        "Intake": "capture the request, business context, stakeholders, goals, constraints, and initial scope",
        "Discovery": "summarize research, current-state findings, user needs, alternatives, assumptions, and open questions",
        "Requirements": "define user needs, functional and non-functional requirements, business rules, traceability, and testable acceptance criteria",
        "Planning": "define work breakdown, sequencing, milestones, estimates, owners, dependencies, risks, and delivery controls",
        "Architecture": "define system boundaries, components, data flows, interfaces, technology decisions, security, scalability, and operational constraints",
        "Design": "define user experience, interaction flows, information architecture, states, accessibility, visual behavior, and implementation-ready design decisions",
        "Development": "define implementation work, technical tasks, coding standards, configuration, error handling, observability, and developer completion criteria",
        "Integration": "define connected systems, contracts, mappings, authentication, synchronization, failure handling, and end-to-end validation",
        "Quality Assurance": "define test strategy, scenarios, test data, automation, defects, regression coverage, and exit criteria",
        "User Acceptance Testing": "define business-user scenarios, expected outcomes, evidence, sign-off owners, and release blockers",
        "Release": "define rollout sequence, migration, feature flags, communications, monitoring, rollback, and go-live criteria",
        "Operations": "define runbooks, monitoring, alerts, on-call ownership, incident response, backups, and service levels",
        "Maintenance": "define support cadence, patching, technical debt, performance monitoring, ownership, and change controls",
        "Retirement": "define decommissioning, data retention or migration, communications, dependencies, and closure evidence",
    }.get(project_stage, f"define the deliverables, decisions, owners, risks, and validation evidence for the {project_stage} stage")
    prompt = (
        "You are a document drafting assistant. Create a complete, professional, publication-ready first draft.\n"
        "The drafting service is not the project, product, customer, or subject of the document. "
        "Never mention the drafting service or its provider in the document. Derive the "
        "project or product name from the user's instructions. If no subject name is "
        "provided, use a neutral title such as \"Product Requirements Document — Project Requirements\" "
        "rather than inventing a provider or project name.\n"
        f"Document type: {document_type}\n"
        f"SDLC stage: {project_stage}\n"
        f"Stage focus: {stage_focus}\n"
        f"User instructions: {text or 'Create a suitable document for this type and stage.'}\n"
        + (
            f"\nCurrent draft to revise:\n{current_content}\n"
            "Revise the current draft according to the user's instructions while preserving useful content."
            if current_content else ""
        )
        + (
            "\nStage constraint: draft for the selected SDLC stage only. Do not substitute a PRD, architecture, "
            "test plan, or generic project summary for the selected stage. The document must make the selected "
            "stage explicit and its sections, terminology, decisions, and acceptance criteria must match that stage. "
            "Use clean Markdown formatting: start with one # title, then use ## section headings, "
            "short paragraphs, and - bullet lists where appropriate. Include practical, specific content "
            "rather than filler. For requirements documents, include objectives, scope, users, functional "
            "requirements, non-functional requirements, assumptions, risks, dependencies, and acceptance "
            "criteria when relevant. Keep terminology consistent and make requirements testable. "
            "When presenting structured attributes, risks, traceability, personas, or comparisons, use a "
            "valid Markdown table with one header row, one separator row made only of --- cells, and one "
            "data row per item; put each row on its own line. For architecture or process diagrams, use "
            "a fenced ```text code block and a readable ASCII diagram with one box/connection per line; "
            "never put a diagram or table on the same line as surrounding prose. Escape pipe characters "
            "inside table cells and keep every table rectangular. "
            "Use the requested project's name as the document subject, not the name of this assistant or service provider. "
            "Return only the document content. Do not mention these instructions, Gemini, prompts, or fallback behavior."
        )
    )
    generated = generate_gemini_text(prompt)
    if generated and _is_structured_draft(generated):
        normalized = _normalize_draft_markdown(generated)
        return _remove_unrequested_provider_references(normalized, text)
    return _fallback_draft(document_type, project_stage, template=template or user_prompt or current_content)


def _is_structured_draft(document: str) -> bool:
    """Accept only a usable Markdown document from the model."""
    text = _normalize_draft_markdown(document)
    heading_count = len(re.findall(r"^#{1,2}\s+\S+", text, flags=re.MULTILINE))
    has_title = bool(re.match(r"^#\s+\S+", text))
    has_body = len(re.findall(r"\S+", text)) >= 120
    return has_title and heading_count >= 4 and has_body


def _normalize_draft_markdown(document: str) -> str:
    """Restore common Markdown block boundaries when a model collapses them."""
    text = html.unescape((document or "").replace("\r\n", "\n")).strip()
    if not text:
        return text
    text = re.sub(r"```([A-Za-z0-9_-]*)\s*", r"\n```\1\n", text)
    text = re.sub(r"\s+(?=(?:#{1,3})\s+\d+\.)", "\n\n", text)
    text = re.sub(r"(?<!\n)(?=\d+\.\s+[A-Z][^|]{2,80}(?:\||$))", "\n\n", text)
    text = re.sub(r"(?<!\n)(?=\|[^|\n]+\|[^|\n]+\|)", "\n", text)
    text = re.sub(r"(?<!\n)(?=\|?\s*:?-{3,}:?\s*\|)", "\n", text)
    text = re.sub(r"(?<!\n)(?=-\s+\*?\*?Given\*?\*?)", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _remove_unrequested_provider_references(document: str, user_prompt: str) -> str:
    """Keep the generated document about the requested subject, not this service."""
    if re.search(r"\bdocflow\b", user_prompt, flags=re.IGNORECASE):
        return document
    return re.sub(r"\bDocFlow(?:'s)?\b", "the platform", document, flags=re.IGNORECASE)


def score_document(document_text: str, document_type: str, project_stage: str) -> dict[str, Any]:
    text = (document_text or "").strip()
    word_count = len(re.findall(r"\S+", text)) if text else 0
    heading_count = len(re.findall(r"^#{1,6}\s+", text, flags=re.MULTILINE))
    bullet_count = len(re.findall(r"^[-*+]\s+", text, flags=re.MULTILINE))
    has_title = bool(re.search(r"^#\s+|^Title\s*:\s*", text, flags=re.MULTILINE))
    has_stage_reference = project_stage.lower() in text.lower() or document_type.lower() in text.lower()
    semantic_review = _semantic_rubric_review(text, document_type, project_stage) if text else None

    structural = min(20, 8 + 5 * min(2, heading_count) + (2 if has_title else 0) + (3 if bullet_count >= 2 else 0))
    completeness = min(
        20,
        max(
            0,
            min(
                20,
                6
                + word_count // 20
                + (2 if has_stage_reference else 0)
                + (4 if heading_count >= 2 else 0)
                + (2 if bullet_count >= 2 else 0),
            ),
        ),
    )
    labeling = min(20, 8 + (4 if has_title else 0) + (4 if has_stage_reference else 0) + (4 if heading_count >= 2 else 0))
    if semantic_review:
        structural = _semantic_points(structural, semantic_review["structural_clarity"]["level"], 20)
        completeness = _semantic_points(completeness, semantic_review["completeness"]["level"], 20)
        labeling = _semantic_points(labeling, semantic_review["labeling_accuracy"]["level"], 20)

    criteria = [
        {
            "name": "Structural clarity",
            "score": structural,
            "max_score": 20,
            "minimum": 12,
            "weight": 1,
            "reason": (
                semantic_review["structural_clarity"]["evidence"]
                if semantic_review
                else "Checked headings, title, and section organization."
            ),
        },
        {
            "name": "Completeness",
            "score": completeness,
            "max_score": 20,
            "minimum": 12,
            "weight": 1,
            "reason": (
                semantic_review["completeness"]["evidence"]
                if semantic_review
                else "Checked how much useful detail is present and whether the required scope is covered."
            ),
        },
        {
            "name": "Labeling accuracy",
            "score": labeling,
            "max_score": 20,
            "minimum": 12,
            "weight": 1.5,
            "reason": (
                semantic_review["labeling_accuracy"]["evidence"]
                if semantic_review
                else "Checked whether the document type and stage are clearly reflected in the content."
            ),
        },
    ]

    total = sum(item["score"] * item["weight"] for item in criteria)
    weighted_max = sum(item["max_score"] * item["weight"] for item in criteria)
    pct = (total / weighted_max) * 100 if weighted_max else 0
    total_points = sum(item["score"] for item in criteria)
    total_threshold = 36
    min_metric_ok = all(item["score"] >= item["minimum"] for item in criteria)
    passed = total_points >= total_threshold and min_metric_ok and pct >= 60
    return {
        "document_type": document_type,
        "project_stage": project_stage,
        "criteria": criteria,
        "total": total_points,
        "threshold": total_threshold,
        "percent": round(pct, 2),
        "passed": passed,
        "clean": passed,
        "status": "clean" if passed else "needs_revision",
        "summary": f"Score: {total_points}/60 ({pct:.2f}%); minimum metric checks: {'pass' if min_metric_ok else 'fail'}.",
        "semantic_review": semantic_review,
    }


def reform_document(document_text: str, document_type: str, project_stage: str, score_result: dict[str, Any] | None = None) -> str:
    cleaned = (document_text or "").strip() or f"{document_type} for {project_stage}"
    sections = [
        f"# {document_type} — {project_stage}",
        "",
        "## Title",
        "A clear title that reflects the document purpose and ownership.",
        "",
        "## Overview",
        "Describe the purpose, business context, and expected result of this document.",
        "",
        "## Scope",
        "- Objective",
        "- In-scope items",
        "- Out-of-scope items",
        "",
        "## Key Details",
        "Capture the main facts, requirements, or process steps that define this deliverable.",
        "",
        "## Acceptance Criteria",
        "- The output is clear and measurable.",
        "- The owner and review path are named.",
        "- The content is aligned to the target stage and document type.",
        "",
        "## Notes",
        "Include open questions, risks, or follow-up actions that still need review.",
    ]
    revised = "\n".join(sections)
    if cleaned and cleaned.lower() not in {"bad text", "n/a"}:
        revised = revised + "\n\n### Original Notes\n" + cleaned
    return revised


def _user_drafts_dir(user_id: str | None = None) -> Path:
    if not user_id:
        return DRAFTS_DIR
    safe_user_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", user_id).strip("._") or "user"
    path = DRAFTS_DIR / safe_user_id
    path.mkdir(exist_ok=True, parents=True)
    return path


def save_document(
    document_text: str,
    document_type: str,
    project_stage: str,
    filename: str | None = None,
    user_id: str | None = None,
) -> dict[str, str]:
    safe_name = (filename or f"{document_type.lower().replace(' ', '_')}_{project_stage.lower().replace(' ', '_')}").strip()
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", safe_name)
    path = _user_drafts_dir(user_id) / f"{safe_name or 'draft'}.md"
    path.write_text(document_text.strip() + "\n", encoding="utf-8")
    return {"path": str(path), "filename": path.name}


def load_saved_documents(user_id: str | None = None) -> list[str]:
    return sorted(p.name for p in _user_drafts_dir(user_id).glob("*.md"))
