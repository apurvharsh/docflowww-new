# DocFlow AI — Backend (FastAPI)

Multi-tenant document workflow API: email/password + Google auth, project-scoped
RBAC/ABAC, hybrid dense+sparse RAG search over uploaded documents (Gemini +
Qdrant), a Drafting Agent, a Scanner Agent (structure/completeness/labeling
score + auto-revision), a Gap-Detection Agent, admin approvals, and an audit
log. This is a pure JSON API — the UI lives in the sibling `../src` React app.

## Setup

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # fill in GEMINI_API_KEY etc. — see comments in the file
```

## Running

```bash
uvicorn app.main:app --reload --port 8000
```

Interactive API docs: http://127.0.0.1:5173/docs (proxied to the internal API)

On first launch it creates `backend/docflow.db` (SQLite), `backend/uploads/`,
`backend/drafts/`, and local Qdrant storage under `backend/qdrant_data/`.

## Logging in

- **Demo**: `POST /auth/demo` — instant admin-level token, no setup required.
- **New organization**: `POST /organizations/signup` creates an organization and
  its first Organization Admin, who can sign in immediately.
- **Email/password**: an organization admin creates a member account. DocFlow
  emails the member their name, organization details, login email, and a
  temporary password. The member must change that password after first login.
- **Google**: configure `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` in `.env`,
  then hit `GET /auth/google/start` (the frontend's "Continue with Google"
  button does this). See `.env.example` for the exact redirect URI to register
  in Google Cloud Console.

## Where each feature lives

| Feature | File |
|---|---|
| Auth (password + Google OAuth), token signing | `app/auth.py`, `app/api/search.py` |
| Role-based + attribute-based access control | `app/authorization.py` |
| Collection-per-tenant Qdrant isolation | `app/ingestion/collection_setup.py` |
| Compound access filter applied at query time (never post-filtered) | `app/retrieval/access_filter.py` |
| Structure-aware chunking + contextual headers | `app/ingestion/chunking.py` |
| Hybrid dense+sparse search, RRF fusion | `app/retrieval/hybrid_search.py` |
| Gemini embeddings + grounded answer generation | `app/retrieval/embeddings.py` |
| Drafting Agent (`/studio/generate-draft`, `/agents/drafting/outline`) | `app/agents/drafting_agent.py`, `app/services/document_workflow.py` |
| Scanner Agent (`/studio/scan-draft`) — rubric score + auto-revision | `app/agents/scanner_agent.py`, `app/services/document_workflow.py` |
| Gap-Detection Agent (`/agents/gap-detection/analyze`) | `app/agents/gap_detection_agent.py` |
| General Query Agent (follow-up suggestions) | `app/agents/general_query_agent.py` |
| Document approval workflow (draft → pending → approved/rejected) | `app/database.py`, `/documents/{id}/submit|approve|reject` |
| Admin: user directory, role assignment, audit log | `/admin/*` routes in `app/api/search.py` |
| RBAC matrix + ABAC policy simulator (for the Admin UI) | `/rbac/matrix`, `/abac/simulate` |
| Personal notes | `/notes`, `app/database.py` |

## Notes

- The local admin login (`AUTH_USERNAME`/`AUTH_PASSWORD` in `.env`) and the
  `/auth/demo` endpoint are meant for local development and demos — swap in
  a real identity provider before exposing this outside your machine.
- Without `GEMINI_API_KEY`, uploads/chunking/auth/RBAC/admin/notes all still
  work; only `/ask` and `/search` (which call Gemini) will return a 503.
- Without `GROQ_API_KEY`, the Drafting/Scanner agents automatically fall back
  to their deterministic template/rubric implementation — nothing breaks.
