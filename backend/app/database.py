"""SQLite application database for projects and uploaded documents."""

import json
import sqlite3
from uuid import uuid4
from datetime import datetime, timezone
from app.config import settings


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _feature_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(settings.feature_database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                project_name TEXT NOT NULL,
                description TEXT,
                owner_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, project_id)
            );
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT,
                provider TEXT NOT NULL,
                provider_subject TEXT UNIQUE,
                tenant_id TEXT NOT NULL,
                is_org_admin INTEGER NOT NULL DEFAULT 0,
                sensitivity_clearance INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                email_verified INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS organizations (
                tenant_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS organization_members (
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                membership_role TEXT NOT NULL DEFAULT 'member',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, user_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_org_members_user
                ON organization_members(user_id, status);
            CREATE TABLE IF NOT EXISTS organization_invitations (
                invitation_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                email TEXT NOT NULL,
                invited_by TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                accepted_at TEXT,
                revoked_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_org_invitations_email
                ON organization_invitations(tenant_id, email, expires_at);
            CREATE TABLE IF NOT EXISTS email_verification_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT
            );
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                stage TEXT NOT NULL,
                doc_type TEXT NOT NULL,
                sensitivity_level INTEGER NOT NULL,
                visible_to_teams TEXT NOT NULL DEFAULT '[]',
                uploaded_by TEXT,
                chunk_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, project_id)
            );
            CREATE TABLE IF NOT EXISTS document_versions (
                version_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                uploaded_by TEXT,
                status TEXT NOT NULL DEFAULT 'indexed',
                created_at TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(document_id)
            );
            CREATE INDEX IF NOT EXISTS idx_documents_project
                ON documents(tenant_id, project_id, created_at);
            CREATE TABLE IF NOT EXISTS workflow_state (
                document_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                approved_by TEXT,
                approval_timestamp TEXT,
                rejection_reason TEXT,
                FOREIGN KEY (document_id) REFERENCES documents(document_id)
            );
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                role TEXT NOT NULL,
                assigned_at TEXT NOT NULL,
                PRIMARY KEY (user_id, project_id)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                log_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                details TEXT,
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_tenant_timestamp
                ON audit_log(tenant_id, timestamp DESC);
            CREATE TABLE IF NOT EXISTS stage_taxonomy (
                tenant_id TEXT NOT NULL,
                stage_name TEXT NOT NULL,
                stage_order INTEGER NOT NULL,
                description TEXT,
                PRIMARY KEY (tenant_id, stage_name)
            );
            CREATE TABLE IF NOT EXISTS notes (
                note_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                project_id TEXT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_notes_owner
                ON notes(tenant_id, owner_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                project_id TEXT,
                mode TEXT NOT NULL DEFAULT 'query',
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner
                ON chat_sessions(tenant_id, user_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS chat_messages (
                message_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session
                ON chat_messages(session_id, created_at);
            """
        )
        _initialize_default_stages()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        if "is_org_admin" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN is_org_admin INTEGER NOT NULL DEFAULT 0")
        if "sensitivity_clearance" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN sensitivity_clearance INTEGER NOT NULL DEFAULT 1")
        for column in ("full_name", "organization", "team_name", "job_title", "manager_email"):
            if column not in columns:
                connection.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT")
        if "must_change_password" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        if "email_verified" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "INSERT OR IGNORE INTO organizations (tenant_id, name, created_at) VALUES (?, ?, ?)",
            (settings.dev_tenant_id, "DocFlow Development Organization", now),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO organization_members (tenant_id, user_id, membership_role, status, created_at)
            SELECT tenant_id, user_id, CASE WHEN is_org_admin = 1 THEN 'admin' ELSE 'member' END,
                   'active', created_at
            FROM users
            """
        )
        connection.execute(
            "UPDATE users SET email_verified = 1 WHERE provider IN ('demo', 'google') AND email_verified = 0"
        )
        document_columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
        if "visible_to_teams" not in document_columns:
            connection.execute("ALTER TABLE documents ADD COLUMN visible_to_teams TEXT NOT NULL DEFAULT '[]'")
        if "uploaded_by" not in document_columns:
            connection.execute("ALTER TABLE documents ADD COLUMN uploaded_by TEXT")
        project_columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
        if "owner_id" not in project_columns:
            connection.execute("ALTER TABLE projects ADD COLUMN owner_id TEXT")
        if "description" not in project_columns:
            connection.execute("ALTER TABLE projects ADD COLUMN description TEXT")
        role_columns = {row[1] for row in connection.execute("PRAGMA table_info(user_roles)")}
        if "assigned_at" not in role_columns:
            timestamp = datetime.now(timezone.utc).isoformat()
            connection.execute("ALTER TABLE user_roles ADD COLUMN assigned_at TEXT")
            connection.execute("UPDATE user_roles SET assigned_at = ?", (timestamp,))
        connection.execute(
            """
            UPDATE user_roles
            SET assigned_at = COALESCE(
                assigned_at,
                (SELECT created_at FROM projects WHERE projects.project_id = user_roles.project_id),
                ?
            )
            WHERE assigned_at IS NULL
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
    initialize_feature_database()


def initialize_feature_database() -> None:
    """Initialize feature-owned storage separately from the core app DB."""
    with _feature_connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS document_versions (
                version_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                uploaded_by TEXT,
                status TEXT NOT NULL DEFAULT 'indexed',
                created_at TEXT NOT NULL,
                UNIQUE (tenant_id, document_id, version_number)
            );
            CREATE INDEX IF NOT EXISTS idx_feature_versions_document
                ON document_versions(tenant_id, document_id, version_number DESC);
            CREATE TABLE IF NOT EXISTS personal_documents (
                document_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_personal_documents_owner
                ON personal_documents(tenant_id, owner_id, created_at DESC);
            """
        )


def create_user(
    *, username: str, password_hash: str | None, provider: str, provider_subject: str | None,
    tenant_id: str,
    full_name: str | None = None, organization: str | None = None,
    team_name: str | None = None, job_title: str | None = None, manager_email: str | None = None,
    is_org_admin: bool = False,
    sensitivity_clearance: int = 1,
    must_change_password: bool = False,
    email_verified: bool = False,
) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    user = {
        "user_id": str(uuid4()),
        "username": username,
        "password_hash": password_hash,
        "provider": provider,
        "provider_subject": provider_subject,
        "tenant_id": tenant_id,
        "is_org_admin": int(is_org_admin),
        "sensitivity_clearance": sensitivity_clearance,
        "created_at": timestamp,
        "full_name": full_name,
        "organization": organization,
        "team_name": team_name,
        "job_title": job_title,
        "manager_email": manager_email,
        "must_change_password": int(must_change_password),
        "email_verified": int(email_verified),
    }
    with _connect() as connection:
        connection.execute(
                """INSERT INTO users
                    (user_id, username, password_hash, provider, provider_subject, tenant_id, is_org_admin, sensitivity_clearance, created_at,
                     full_name, organization, team_name, job_title, manager_email, must_change_password, email_verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(user.values()),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO organization_members
                (tenant_id, user_id, membership_role, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (tenant_id, user["user_id"], "admin" if is_org_admin else "member", timestamp),
        )
    return user


def create_organization(tenant_id: str, name: str) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO organizations (tenant_id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, name.strip(), datetime.now(timezone.utc).isoformat()),
        )


def create_organization_invitation(
    *, tenant_id: str, email: str, invited_by: str, token_hash: str, expires_at: str
) -> dict:
    invitation = {
        "invitation_id": str(uuid4()),
        "tenant_id": tenant_id,
        "email": email.strip().lower(),
        "invited_by": invited_by,
        "token_hash": token_hash,
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO organization_invitations
                (invitation_id, tenant_id, email, invited_by, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(invitation.values()),
        )
    return invitation


def get_valid_organization_invitation(token_hash: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM organization_invitations
            WHERE token_hash = ? AND accepted_at IS NULL AND revoked_at IS NULL
              AND expires_at > ?
            """,
            (token_hash, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    return dict(row) if row else None


def accept_organization_invitation(invitation_id: str) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE organization_invitations SET accepted_at = ? WHERE invitation_id = ?",
            (datetime.now(timezone.utc).isoformat(), invitation_id),
        )


def create_email_verification_token(token_hash: str, user_id: str, expires_at: str) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO email_verification_tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )


def consume_email_verification_token(token_hash: str) -> str | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT user_id FROM email_verification_tokens
            WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
            """,
            (token_hash, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
        if not row:
            return None
        user_id = row["user_id"]
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "UPDATE email_verification_tokens SET used_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )
        connection.execute("UPDATE users SET email_verified = 1 WHERE user_id = ?", (user_id,))
    return user_id


def mark_user_email_verified(user_id: str) -> None:
    with _connect() as connection:
        connection.execute("UPDATE users SET email_verified = 1 WHERE user_id = ?", (user_id,))


def set_password(user_id: str, password_hash: str, must_change_password: bool = False) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE users SET password_hash = ?, must_change_password = ? WHERE user_id = ?",
            (password_hash, int(must_change_password), user_id),
        )


def create_password_reset_token(token_hash: str, user_id: str, expires_at: str) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO password_reset_tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )


def consume_password_reset_token(token_hash: str) -> str | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT user_id FROM password_reset_tokens WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
            (token_hash, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
        if not row:
            return None
        connection.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ?",
            (datetime.now(timezone.utc).isoformat(), token_hash),
        )
    return row["user_id"]


def get_user_by_username(username: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    """Find an account case-insensitively so one email cannot create duplicates."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE lower(username) = lower(?)",
            (email.strip(),),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def delete_user(user_id: str, tenant_id: str) -> bool:
    """Remove a user and their project memberships from an organization."""
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM users WHERE user_id = ? AND tenant_id = ?",
            (user_id, tenant_id),
        )
    return cursor.rowcount > 0


def get_user_by_provider_subject(provider: str, provider_subject: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE provider = ? AND provider_subject = ?",
            (provider, provider_subject),
        ).fetchone()
    return dict(row) if row else None


def link_user_provider(user_id: str, provider: str, provider_subject: str) -> None:
    """Link an already-registered email account to an external identity."""
    with _connect() as connection:
        connection.execute(
            "UPDATE users SET provider = ?, provider_subject = ? WHERE user_id = ?",
            (provider, provider_subject, user_id),
        )


def get_user_access_context(user_id: str):
    """Resolve a persisted user and project memberships into UserContext."""
    from app.models.schema import UserContext

    user = get_user_by_id(user_id)
    if not user:
        return None
    with _connect() as connection:
        membership = connection.execute(
            """
            SELECT 1 FROM organization_members
            WHERE tenant_id = ? AND user_id = ? AND status = 'active'
            """,
            (user["tenant_id"], user_id),
        ).fetchone()
        if not membership:
            return None
        rows = connection.execute(
            """
            SELECT ur.project_id, ur.role
            FROM user_roles ur
            JOIN projects p ON p.project_id = ur.project_id
            JOIN users u ON u.user_id = ur.user_id AND u.tenant_id = p.tenant_id
            WHERE ur.user_id = ? AND p.tenant_id = ?
            """,
            (user_id, user["tenant_id"]),
        ).fetchall()
        owned_projects = connection.execute(
            "SELECT project_id FROM projects WHERE tenant_id = ? AND owner_id = ?",
            (user["tenant_id"], user_id),
        ).fetchall()
    project_roles = {row["project_id"]: row["role"] for row in rows}
    for project in owned_projects:
        project_roles.setdefault(project["project_id"], "member")
    team_memberships = {
        project_id: [user["team_name"]]
        for project_id in project_roles
        if user.get("team_name")
    }
    role = "admin" if user.get("is_org_admin") or any(row["role"] == "admin" for row in rows) else "member"
    if role != "admin" and any(row["role"] == "reviewer" for row in rows):
        role = "reviewer"
    return UserContext(
        user_id=user["user_id"],
        tenant_id=user["tenant_id"],
        is_org_admin=bool(user.get("is_org_admin")),
        role=role,
        project_roles=project_roles,
        team_memberships=team_memberships,
        sensitivity_clearance=user.get("sensitivity_clearance", 1),
        must_change_password=bool(user.get("must_change_password")),
    )


def list_users(tenant_id: str, project_ids: list[str] | None = None) -> list[dict]:
    """List users and their project roles within one tenant."""
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT u.user_id, u.username, u.full_name, u.team_name, u.is_org_admin,
                   ur.project_id, ur.role
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.user_id
            WHERE u.tenant_id = ?
            ORDER BY u.username, ur.project_id
            """,
            (tenant_id,),
        ).fetchall()
    users: dict[str, dict] = {}
    for row in rows:
        user = users.setdefault(row["user_id"], {
            "user_id": row["user_id"], "username": row["username"],
            "full_name": row["full_name"], "team_name": row["team_name"],
            "is_org_admin": bool(row["is_org_admin"]), "roles": [],
        })
        if row["project_id"]:
            user["roles"].append({"project_id": row["project_id"], "role": row["role"]})
    result = list(users.values())
    if project_ids is None:
        return result
    allowed = set(project_ids)
    return [
        user for user in result
        if any(role["project_id"] in allowed for role in user["roles"])
    ]


def record_document(
    *,
    tenant_id: str,
    project_id: str,
    project_name: str,
    document_id: str,
    filename: str,
    stored_path: str,
    stage: str,
    doc_type: str,
    sensitivity_level: int,
    chunk_count: int,
    visible_to_teams: list[str] | None = None,
    uploaded_by: str | None = None,
) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO projects (tenant_id, project_id, project_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, project_id) DO UPDATE SET
                project_name = excluded.project_name,
                updated_at = excluded.updated_at
            """,
            (tenant_id, project_id, project_name, timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO documents (
                document_id, tenant_id, project_id, filename, stored_path,
                stage, doc_type, sensitivity_level, visible_to_teams, uploaded_by, chunk_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO NOTHING
            """,
            (
                document_id,
                tenant_id,
                project_id,
                filename,
                stored_path,
                stage,
                doc_type,
                sensitivity_level,
                json.dumps(visible_to_teams or []),
                uploaded_by,
                chunk_count,
                timestamp,
            ),
        )


def list_projects(tenant_id: str) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT p.project_id, p.project_name, p.description, p.owner_id, p.created_at, p.updated_at,
                   COUNT(d.document_id) AS document_count
            FROM projects p
            LEFT JOIN documents d ON d.tenant_id = p.tenant_id AND d.project_id = p.project_id
            WHERE p.tenant_id = ?
            GROUP BY p.tenant_id, p.project_id, p.project_name
            ORDER BY p.updated_at DESC
            """,
            (tenant_id,),
        ).fetchall()
    projects = [dict(row) for row in rows]
    with _connect() as connection:
        member_rows = connection.execute(
            """
            SELECT ur.project_id, ur.role,
                   COALESCE(
                       (
                           SELECT MAX(a.timestamp)
                           FROM audit_log a
                           WHERE a.tenant_id = u.tenant_id
                             AND a.action = 'ASSIGN_PROJECT_ACCESS'
                             AND a.resource_id = u.user_id
                             AND a.details LIKE '%project=' || ur.project_id || ';%'
                       ),
                       ur.assigned_at,
                       p.created_at
                   ) AS assigned_at,
                   u.user_id, u.full_name, u.username, u.team_name
            FROM user_roles ur
            JOIN users u ON u.user_id = ur.user_id
            JOIN projects p ON p.project_id = ur.project_id AND p.tenant_id = u.tenant_id
            WHERE u.tenant_id = ?
            ORDER BY ur.project_id, COALESCE(u.full_name, u.username)
            """,
            (tenant_id,),
        ).fetchall()
    members_by_project: dict[str, list[dict]] = {}
    for row in member_rows:
        members_by_project.setdefault(row["project_id"], []).append({
            "user_id": row["user_id"],
            "name": row["full_name"] or row["username"],
            "username": row["username"],
            "team_name": row["team_name"],
            "role": row["role"],
            "access_granted_at": row["assigned_at"],
        })
    for project in projects:
        project["members"] = members_by_project.get(project["project_id"], [])
        project["created_at"] = project.get("created_at") or project.get("updated_at")
        for member in project["members"]:
            member["access_granted_at"] = member.get("access_granted_at") or project["created_at"]
    return projects


def create_project(tenant_id: str, project_id: str, project_name: str, owner_id: str, description: str | None = None) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            "INSERT INTO projects (tenant_id, project_id, project_name, description, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, project_id, project_name, description, owner_id, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO user_roles (user_id, project_id, role, assigned_at) VALUES (?, ?, 'member', ?)",
            (owner_id, project_id, timestamp),
        )
    return {
        "project_id": project_id,
        "project_name": project_name,
        "description": description,
        "owner_id": owner_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "document_count": 0,
        "members": [{"user_id": owner_id, "name": owner_id, "username": owner_id, "team_name": None, "role": "member", "access_granted_at": timestamp}],
    }


def create_note(tenant_id: str, owner_id: str, title: str, content: str, project_id: str | None = None) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    note = {"note_id": str(uuid4()), "tenant_id": tenant_id, "owner_id": owner_id, "project_id": project_id, "title": title, "content": content, "created_at": timestamp, "updated_at": timestamp}
    with _connect() as connection:
        connection.execute(
            "INSERT INTO notes (note_id, tenant_id, owner_id, project_id, title, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(note.values()),
        )
    return note


def list_notes(tenant_id: str, owner_id: str) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM notes WHERE tenant_id = ? AND owner_id = ? ORDER BY updated_at DESC",
            (tenant_id, owner_id),
        ).fetchall()
    return [dict(row) for row in rows]


def create_chat_session(tenant_id: str, user_id: str, project_id: str | None, mode: str, title: str) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    session = {
        "session_id": str(uuid4()),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "project_id": project_id,
        "mode": mode,
        "title": title.strip() or "New conversation",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with _connect() as connection:
        connection.execute(
            "INSERT INTO chat_sessions (session_id, tenant_id, user_id, project_id, mode, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(session.values()),
        )
    return session


def list_chat_sessions(tenant_id: str, user_id: str, project_id: str | None, mode: str) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM chat_sessions
            WHERE tenant_id = ? AND user_id = ? AND mode = ?
              AND ((project_id IS NULL AND ? IS NULL) OR project_id = ?)
            ORDER BY updated_at DESC
            """,
            (tenant_id, user_id, mode, project_id, project_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_chat_session(tenant_id: str, user_id: str, session_id: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM chat_sessions WHERE tenant_id = ? AND user_id = ? AND session_id = ?",
            (tenant_id, user_id, session_id),
        ).fetchone()
    return dict(row) if row else None


def add_chat_message(session_id: str, role: str, content: str, sources: list[dict] | None = None) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    message = {
        "message_id": str(uuid4()),
        "session_id": session_id,
        "role": role,
        "content": content,
        "sources": json.dumps(sources or []),
        "created_at": timestamp,
    }
    with _connect() as connection:
        connection.execute(
            "INSERT INTO chat_messages (message_id, session_id, role, content, sources, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            tuple(message.values()),
        )
        connection.execute("UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?", (timestamp, session_id))
    message["sources"] = sources or []
    return message


def list_chat_messages(session_id: str) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    messages = []
    for row in rows:
        message = dict(row)
        message["sources"] = json.loads(message["sources"] or "[]")
        messages.append(message)
    return messages


def delete_chat_session(tenant_id: str, user_id: str, session_id: str) -> bool:
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM chat_sessions WHERE tenant_id = ? AND user_id = ? AND session_id = ?",
            (tenant_id, user_id, session_id),
        )
    return cursor.rowcount > 0


def get_project(tenant_id: str, project_id: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM projects WHERE tenant_id = ? AND project_id = ?",
            (tenant_id, project_id),
        ).fetchone()
    return dict(row) if row else None


def list_documents(tenant_id: str, project_id: str) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT d.document_id, d.project_id, d.filename, d.stage, d.doc_type,
                   d.sensitivity_level, d.chunk_count, d.created_at,
                   d.visible_to_teams, d.uploaded_by,
                   COALESCE(w.state, 'draft') AS workflow_state
            FROM documents d
            LEFT JOIN workflow_state w ON d.document_id = w.document_id
            WHERE d.tenant_id = ? AND d.project_id = ?
            ORDER BY d.created_at DESC
            """,
            (tenant_id, project_id),
        ).fetchall()
    return [dict(row) for row in rows]


def create_document_version(
    tenant_id: str, document_id: str, filename: str, stored_path: str, file_size_bytes: int,
    uploaded_by: str | None = None, status: str = "indexed",
) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    version_id = str(uuid4())
    with _feature_connect() as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(version_number), 0) AS latest FROM document_versions WHERE tenant_id = ? AND document_id = ?",
            (tenant_id, document_id),
        ).fetchone()
        version = {
            "version_id": version_id,
            "document_id": document_id,
            "tenant_id": tenant_id,
            "version_number": int(row["latest"]) + 1,
            "filename": filename,
            "stored_path": stored_path,
            "file_size_bytes": file_size_bytes,
            "uploaded_by": uploaded_by,
            "status": status,
            "created_at": timestamp,
        }
        connection.execute(
            """INSERT INTO document_versions
            (version_id, tenant_id, document_id, version_number, filename, stored_path,
             file_size_bytes, uploaded_by, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(version.values()),
        )
    return version


def list_document_versions(tenant_id: str, document_id: str) -> list[dict]:
    with _feature_connect() as connection:
        rows = connection.execute(
            """SELECT * FROM document_versions
            WHERE tenant_id = ? AND document_id = ?
            ORDER BY version_number DESC""",
            (tenant_id, document_id),
        ).fetchall()
    return [dict(row) for row in rows]


def create_personal_document(
    tenant_id: str, owner_id: str, filename: str, stored_path: str, file_size_bytes: int,
) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    document = {
        "document_id": str(uuid4()),
        "tenant_id": tenant_id,
        "owner_id": owner_id,
        "filename": filename,
        "stored_path": stored_path,
        "file_size_bytes": file_size_bytes,
        "created_at": timestamp,
    }
    with _feature_connect() as connection:
        connection.execute(
            """INSERT INTO personal_documents
            (document_id, tenant_id, owner_id, filename, stored_path, file_size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            tuple(document.values()),
        )
    return document


def list_personal_documents(tenant_id: str, owner_id: str) -> list[dict]:
    with _feature_connect() as connection:
        rows = connection.execute(
            """SELECT * FROM personal_documents
            WHERE tenant_id = ? AND owner_id = ? ORDER BY created_at DESC""",
            (tenant_id, owner_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_document(tenant_id: str, document_id: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT d.*, COALESCE(w.state, 'draft') AS workflow_state
            FROM documents d
            LEFT JOIN workflow_state w ON w.document_id = d.document_id
            WHERE d.tenant_id = ? AND d.document_id = ?
            """,
            (tenant_id, document_id),
        ).fetchone()
    return dict(row) if row else None


def delete_document(tenant_id: str, document_id: str) -> bool:
    with _connect() as connection:
        connection.execute("DELETE FROM workflow_state WHERE document_id = ?", (document_id,))
        cursor = connection.execute(
            "DELETE FROM documents WHERE tenant_id = ? AND document_id = ?",
            (tenant_id, document_id),
        )
    with _feature_connect() as connection:
        connection.execute("DELETE FROM document_versions WHERE tenant_id = ? AND document_id = ?", (tenant_id, document_id))
    return cursor.rowcount > 0


def _initialize_default_stages() -> None:
    """Ensure default SDLC stages exist for each tenant."""
    default_stages = [
        ("Intake", 0),
        ("Discovery", 1),
        ("Requirements", 2),
        ("Planning", 3),
        ("Architecture", 4),
        ("Design", 5),
        ("Development", 6),
        ("Integration", 7),
        ("Quality Assurance", 8),
        ("User Acceptance Testing", 9),
        ("Release", 10),
        ("Operations", 11),
        ("Maintenance", 12),
        ("Retirement", 13),
    ]
    with _connect() as connection:
        for stage_name, order in default_stages:
            connection.execute(
                "INSERT OR IGNORE INTO stage_taxonomy (tenant_id, stage_name, stage_order, description) VALUES (?, ?, ?, ?)",
                (settings.dev_tenant_id, stage_name, order, f"SDLC stage: {stage_name}"),
            )


def get_valid_stages(tenant_id: str) -> list[str]:
    """Get valid SDLC stages for a tenant, ordered by sequence."""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT stage_name FROM stage_taxonomy WHERE tenant_id = ? ORDER BY stage_order",
            (tenant_id,),
        ).fetchall()
    return [row[0] for row in rows]


def get_user_role(user_id: str, project_id: str) -> str | None:
    """Get a user's role in a specific project (member, reviewer, admin)."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT role FROM user_roles WHERE user_id = ? AND project_id = ?",
            (user_id, project_id),
        ).fetchone()
    return row[0] if row else None


def set_user_role(user_id: str, project_id: str, role: str) -> None:
    """Assign a role to a user in a project."""
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO user_roles (user_id, project_id, role, assigned_at) VALUES (?, ?, ?, ?)",
            (user_id, project_id, role, datetime.now(timezone.utc).isoformat()),
        )


def remove_user_role(user_id: str, project_id: str) -> bool:
    """Remove a user's membership from a project."""
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM user_roles WHERE user_id = ? AND project_id = ?",
            (user_id, project_id),
        )
        connection.execute(
            "UPDATE projects SET owner_id = NULL WHERE project_id = ? AND owner_id = ?",
            (project_id, user_id),
        )
    return cursor.rowcount > 0


def update_user_access(
    user_id: str,
    project_id: str | None = None,
    role: str | None = None,
    team_name: str | None = None,
    sensitivity_clearance: int | None = None,
    is_org_admin: bool | None = None,
) -> None:
    """Update persisted RBAC/ABAC attributes for a tenant user."""
    with _connect() as connection:
        if team_name is not None or sensitivity_clearance is not None or is_org_admin is not None:
            fields = []
            values = []
            if team_name is not None:
                fields.append("team_name = ?")
                values.append(team_name)
            if sensitivity_clearance is not None:
                fields.append("sensitivity_clearance = ?")
                values.append(sensitivity_clearance)
            if is_org_admin is not None:
                fields.append("is_org_admin = ?")
                values.append(int(is_org_admin))
            values.append(user_id)
            connection.execute(f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?", values)
        if project_id and role:
            connection.execute(
                "INSERT OR REPLACE INTO user_roles (user_id, project_id, role, assigned_at) VALUES (?, ?, ?, ?)",
                (user_id, project_id, role, datetime.now(timezone.utc).isoformat()),
            )


def get_document_workflow_state(document_id: str) -> str:
    """Get the current approval workflow state of a document."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT state FROM workflow_state WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    return row[0] if row else "draft"


def submit_document(document_id: str) -> None:
    """Move a draft into the approval queue."""
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO workflow_state (document_id, state) VALUES (?, ?)",
            (document_id, "pending_review"),
        )


def approve_document(document_id: str, approved_by: str) -> None:
    """Mark a document as approved."""
    timestamp = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO workflow_state (document_id, state, approved_by, approval_timestamp) VALUES (?, ?, ?, ?)",
            (document_id, "approved", approved_by, timestamp),
        )


def reject_document(document_id: str, rejection_reason: str) -> None:
    """Mark a document as rejected."""
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO workflow_state (document_id, state, rejection_reason) VALUES (?, ?, ?)",
            (document_id, "rejected", rejection_reason),
        )


def audit_log(tenant_id: str, user_id: str, action: str, resource_type: str, resource_id: str | None = None, details: str | None = None) -> None:
    """Log an audit event."""
    timestamp = datetime.now(timezone.utc).isoformat()
    log_id = str(uuid4())
    with _connect() as connection:
        connection.execute(
            "INSERT INTO audit_log (log_id, tenant_id, user_id, action, resource_type, resource_id, details, timestamp, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (log_id, tenant_id, user_id, action, resource_type, resource_id, details, timestamp, timestamp),
        )
    if action in {"ASSIGN_PROJECT_ACCESS", "SET_ROLE"} and resource_id:
        _send_access_change_email(
            tenant_id=tenant_id,
            target_user_id=resource_id,
            action=action,
            details=details or "",
        )


def _send_access_change_email(
    *, tenant_id: str, target_user_id: str, action: str, details: str
) -> None:
    """Notify the affected user before the access-changing request completes."""
    target = get_user_by_id(target_user_id)
    if not target or target.get("tenant_id") != tenant_id:
        return
    project_id = _detail_value(details, "project") or "a project"
    project = get_project(tenant_id, project_id) if project_id != "a project" else None
    project_name = project.get("project_name") if project else project_id
    raw_role = _detail_value(details, "role") or "member"
    role_labels = {
        "admin": "Project Admin",
        "reviewer": "Project Admin",
        "team_lead": "Team Lead",
        "member": "Member",
    }
    role_name = role_labels.get(raw_role, raw_role)
    team_name = _detail_value(details, "team") or (target.get("team_name") or "Not specified")
    scope = {
        "Project Admin": "All teams and project workflows",
        "Team Lead": "Role management for your assigned team",
        "Member": "Standard access within your assigned team",
    }.get(role_name, "Project-specific access")
    subject = f"DocFlow project access: {project_name}"
    if action in {"ASSIGN_PROJECT_ACCESS", "SET_ROLE"}:
        body = (
            "Your DocFlow project access has been assigned or updated.\n\n"
            f"Project: {project_name} ({project_id})\n"
            f"Role: {role_name}\n"
            f"Team: {team_name}\n"
            f"Access scope: {scope}\n\n"
            "Sign in to view the project and use the permissions assigned to you."
        )
    else:
        body = (
            f"Your access to project {project_id} in DocFlow has been removed.\n\n"
            "Contact an organization administrator if you need access restored."
        )
    from app.services.email_notifications import send_notification_email

    send_notification_email(target["username"], subject, body)


def _detail_value(details: str, key: str) -> str | None:
    prefix = f"{key}="
    for item in details.split(";"):
        if item.startswith(prefix):
            return item[len(prefix):] or None
    return None


def get_audit_log(tenant_id: str, limit: int = 100) -> list[dict]:
    """Retrieve audit log entries for a tenant."""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM audit_log WHERE tenant_id = ? ORDER BY timestamp DESC LIMIT ?",
            (tenant_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_user_notifications(tenant_id: str, user_id: str, is_org_admin: bool, limit: int = 100) -> list[dict]:
    """Return the tenant feed for admins or a project-scoped feed for members."""
    member_filter = """
              AND (
                (
                    a.resource_id = ?
                    AND a.action IN ('ASSIGN_PROJECT_ACCESS', 'REMOVE_PROJECT_ACCESS', 'ASSIGN_ACCESS')
                )
                OR EXISTS (
                    SELECT 1
                    FROM user_roles ur
                    WHERE ur.user_id = ?
                      AND (
                        (a.resource_type = 'project' AND a.resource_id = ur.project_id)
                        OR a.details LIKE '%project=' || ur.project_id || '%'
                        OR EXISTS (
                            SELECT 1
                            FROM documents d
                            WHERE d.tenant_id = a.tenant_id
                              AND d.document_id = a.resource_id
                              AND d.project_id = ur.project_id
                        )
                      )
                )
              )
    """
    query = """
            SELECT a.*, COALESCE(u.full_name, u.username, a.user_id) AS actor_name
            FROM audit_log a
            LEFT JOIN users u ON u.user_id = a.user_id
            WHERE a.tenant_id = ?
    """
    parameters: list = [tenant_id]
    if not is_org_admin:
        query += member_filter
        parameters.extend([user_id, user_id])
    query += """
            ORDER BY a.timestamp DESC
            LIMIT ?
            """
    parameters.append(limit)
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def get_project_activity(tenant_id: str, project_id: str, limit: int = 200) -> list[dict]:
    """Return project-scoped audit events with actor identity for administrators."""
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT a.*, COALESCE(u.full_name, u.username, a.user_id) AS actor_name,
                   d.filename, d.stage AS document_stage
            FROM audit_log a
            LEFT JOIN users u ON u.user_id = a.user_id
            LEFT JOIN documents d ON d.tenant_id = a.tenant_id AND d.document_id = a.resource_id
            WHERE a.tenant_id = ?
              AND (
                (a.resource_type = 'project' AND a.resource_id = ?)
                OR a.details LIKE ?
                OR (d.project_id = ?)
              )
            ORDER BY a.timestamp DESC
            LIMIT ?
            """,
            (tenant_id, project_id, f"%project={project_id}%", project_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def list_all_documents(tenant_id: str) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT d.document_id, d.project_id, COALESCE(p.project_name, d.project_id) AS project_name,
                   d.filename, d.stored_path, d.stage, d.doc_type, d.sensitivity_level,
                     d.chunk_count, d.created_at, d.visible_to_teams, d.uploaded_by,
                     COALESCE(w.state, 'draft') AS workflow_state
            FROM documents d
            LEFT JOIN projects p ON p.tenant_id = d.tenant_id AND p.project_id = d.project_id
                 LEFT JOIN workflow_state w ON w.document_id = d.document_id
            WHERE d.tenant_id = ?
            ORDER BY d.created_at DESC
            """,
            (tenant_id,),
        ).fetchall()
    return [dict(row) for row in rows]
