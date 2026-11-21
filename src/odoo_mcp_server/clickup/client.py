"""Native ClickUp REST client (API v2, plus v3 where v2 can't do the job).

Ported from the cos-mcp-public ClickUp integration. Every call names one of the
configured accounts (``CLICKUP_ACCOUNTS``), resolves its token and talks to
``api.clickup.com`` directly. Tokens are never returned nor included in errors.

Rules carried over from the original design:
- All configured tokens must be distinct (checked at construction).
- An account with an expected user id (``CLICKUP_ACCOUNT_USER_IDS``) is verified
  against ``GET /user`` before its first use; only success is cached.
- A failing account is never retried with a different account.
- Everything is scoped to a single Workspace (``CLICKUP_WORKSPACE_ID``).
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

API_V2 = "https://api.clickup.com/api/v2"
API_V3 = "https://api.clickup.com/api/v3"
PRIORITIES = {"urgent": 1, "high": 2, "normal": 3, "low": 4}
_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_MAX_RATE_WAIT = 20.0
_CACHE_TTL = 300.0
_MAX_SCAN_PAGES = 5  # 100 tasks per ClickUp page
_PAGE_SIZE = 100
_MAX_DESCRIPTION_CHARS = 8000
_MAX_COMMENT_CHARS = 4000
_CHECKLIST_ACTIONS = ("list", "add", "add_item", "check", "uncheck", "rename_item", "remove_item")


class ClickUpError(RuntimeError):
    """Actionable ClickUp error; never contains a token."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class ClickUpConfig:
    """Accounts (name -> API token), optional identity pins and the Workspace scope."""

    accounts: dict[str, str]
    workspace_id: str
    expected_user_ids: dict[str, int] = field(default_factory=dict)
    default_account: str | None = None
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        if not self.accounts:
            raise ValueError("CLICKUP_ACCOUNTS must define at least one account")
        if not self.workspace_id:
            raise ValueError("CLICKUP_WORKSPACE_ID is required")
        tokens = [t.strip() for t in self.accounts.values()]
        if any(not t for t in tokens):
            raise ValueError("Every CLICKUP_ACCOUNTS entry needs a non-empty token")
        if len(set(tokens)) != len(tokens):
            raise ValueError("CLICKUP_ACCOUNTS tokens must be distinct per account")
        unknown = set(self.expected_user_ids) - set(self.accounts)
        if unknown:
            raise ValueError(f"CLICKUP_ACCOUNT_USER_IDS names unknown accounts: {sorted(unknown)}")
        if self.default_account and self.default_account not in self.accounts:
            raise ValueError(f"CLICKUP_DEFAULT_ACCOUNT {self.default_account!r} is not in CLICKUP_ACCOUNTS")
        ZoneInfo(self.timezone)  # fail fast on a bad zone name

    @property
    def account_names(self) -> list[str]:
        return list(self.accounts)

    @property
    def effective_default_account(self) -> str:
        return self.default_account or next(iter(self.accounts))


# --------------------------------------------------------------------------- helpers


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _ambiguous(kind: str, ref: str, candidates: Iterable[str]) -> ValueError:
    shown = list(candidates)[:15]
    return ValueError(f"{kind} {ref!r} is ambiguous: {'; '.join(shown)}. Use the ID or full path.")


def _not_found(kind: str, ref: str, candidates: Iterable[str]) -> ValueError:
    near = [c for c in candidates if _norm(ref) in _norm(c)][:10]
    hint = f" Similar: {'; '.join(near)}." if near else " Use clickup_hierarchy to see the options."
    return ValueError(f"No {kind} {ref!r}.{hint}")


def _priority_value(value: str | None) -> int | None:
    if value is None:
        return None
    key = str(value).strip().lower()
    if key in ("", "none", "clear"):
        return None
    if key not in PRIORITIES:
        raise ValueError(f"Invalid priority {value!r}: use urgent, high, normal, low or none")
    return PRIORITIES[key]


def _task_id(task_id: Any) -> str:
    value = str(task_id or "").strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError(f"Invalid task ID: {task_id!r}")
    return value


def _match_status(wanted: str, valid: list[str]) -> str:
    hit = {_norm(v): v for v in valid}.get(_norm(wanted))
    if hit is None:
        raise ValueError(f"Status {wanted!r} does not exist in the list. Valid: {', '.join(valid)}")
    return hit


def _match_field(fields: list[dict], ref: str) -> dict:
    hits = [f for f in fields if f.get("id") == ref] or [
        f for f in fields if _norm(f.get("name", "")) == _norm(ref)
    ]
    if len(hits) == 1:
        return hits[0]
    names = [f"{f.get('name')} ({f.get('type')})" for f in fields]
    if hits:
        raise _ambiguous("Field", ref, names)
    raise ValueError(f"No field {ref!r}. Fields: {', '.join(names) or 'none'}")


def _shape_field(f: dict) -> dict:
    value = f.get("value")
    options = (f.get("type_config") or {}).get("options") or []
    if f.get("type") == "drop_down" and options:
        for opt in options:
            if opt.get("orderindex") == value or opt.get("id") == value:
                value = opt.get("name")
                break
    elif f.get("type") == "labels" and options and isinstance(value, list):
        names = {opt.get("id"): opt.get("label") or opt.get("name") for opt in options}
        value = [names.get(v, v) for v in value]
    return {"id": f.get("id"), "name": f.get("name"), "type": f.get("type"), "value": value}


def _shape_checklists(raw: list[dict]) -> list[dict]:
    return [{
        "id": c.get("id"), "name": c.get("name"),
        "items": [{"id": i.get("id"), "name": i.get("name"), "resolved": bool(i.get("resolved"))}
                  for i in c.get("items") or []],
    } for c in raw]


def _compact(data: dict) -> dict:
    return {k: v for k, v in data.items() if v not in (None, [], "")}


def _error_from(response: httpx.Response, account: str) -> ClickUpError:
    status = response.status_code
    code = message = None
    try:
        body = response.json()
        if isinstance(body, dict):
            message = body.get("err") or body.get("error") or body.get("message")
            code = body.get("ECODE")
    except ValueError:
        pass
    detail = f"{message} ({code})" if message and code else (message or response.reason_phrase or "")
    if status == 401:
        text = f"ClickUp rejected the token of account {account!r} (401). Check CLICKUP_ACCOUNTS."
    elif status == 403:
        text = f"Account {account!r} is not allowed to do this in ClickUp (403): {detail}"
    elif status == 404:
        text = f"ClickUp could not find the resource (404): {detail}"
    elif status == 429:
        text = "ClickUp rate limit exhausted (429); wait a minute and retry."
    elif status >= 500:
        text = (f"ClickUp answered {status}: {detail}. ClickUp often answers 500 to invalid "
                "filters; narrow or change the filters.")
    else:
        text = f"ClickUp answered {status}: {detail}"
    return ClickUpError(text.strip(), status=status, code=code)


# --------------------------------------------------------------------------- client


class ClickUpClient:
    """Thread-safe, synchronous ClickUp client. Call it via ``asyncio.to_thread``."""

    def __init__(self, config: ClickUpConfig, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.tz = ZoneInfo(config.timezone)
        self._http = httpx.Client(timeout=_TIMEOUT, transport=transport)
        self._lock = threading.Lock()
        self._verified: set[str] = set()
        self._hierarchy_cache: dict[str, tuple[float, dict]] = {}
        self._members_cache: dict[str, tuple[float, list[dict]]] = {}
        self._me_cache: dict[str, dict] = {}

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ accounts / HTTP

    def check_account(self, account: Any) -> str:
        if account in (None, ""):
            return self.config.effective_default_account
        if not isinstance(account, str) or account not in self.config.accounts:
            raise ValueError(f"Unknown ClickUp account {account!r}; use one of: "
                             f"{', '.join(self.config.account_names)}")
        return account

    def _token(self, account: str) -> str:
        token = self.config.accounts[account].strip()
        expected = self.config.expected_user_ids.get(account)
        if expected is not None:
            self._verify_identity(account, token, expected)
        return token

    def _verify_identity(self, account: str, token: str, expected: int) -> None:
        with self._lock:
            if account in self._verified:
                return
        user = self._send(token, "GET", f"{API_V2}/user", account=account).get("user") or {}
        if user.get("id") != expected:
            raise ClickUpError(f"The API key of account {account!r} does not belong to the expected user")
        with self._lock:
            self._verified.add(account)

    def _send(self, token: str, method: str, url: str, *, account: str, params: Any = None,
              json: Any = None) -> dict:
        for attempt in (0, 1):
            try:
                response = self._http.request(
                    method, url, params=params, json=json,
                    headers={"Authorization": token, "Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise ClickUpError(f"Could not reach ClickUp ({type(exc).__name__})") from None
            if response.status_code == 429 and attempt == 0 and method == "GET":
                # Only reads are retried: a repeated write could be applied twice.
                time.sleep(self._rate_wait(response))
                continue
            if response.status_code >= 400:
                raise _error_from(response, account)
            if not response.content:
                return {}
            try:
                body = response.json()
            except ValueError:
                raise ClickUpError("ClickUp returned a non-JSON response") from None
            return body if isinstance(body, dict) else {"data": body}
        raise ClickUpError("ClickUp rate limit exhausted (429); wait a minute and retry.", status=429)

    @staticmethod
    def _rate_wait(response: httpx.Response) -> float:
        reset = response.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            return max(0.5, min(_MAX_RATE_WAIT, int(reset) - time.time()))
        return 2.0

    def request(self, account: str, method: str, path: str, *, params: Any = None,
                json: Any = None, v3: bool = False) -> dict:
        """Authenticated request as ``account``. ``path`` starts with '/'."""
        token = self._token(account)
        return self._send(token, method, f"{API_V3 if v3 else API_V2}{path}", account=account,
                          params=params, json=json)

    # ------------------------------------------------------------------ dates

    def _ms_to_iso(self, value: Any) -> str | None:
        if value in (None, "", 0, "0"):
            return None
        try:
            dt = datetime.fromtimestamp(int(value) / 1000, tz=self.tz)
        except (TypeError, ValueError):
            return None
        return dt.isoformat(timespec="minutes")

    def parse_date(self, value: str, *, end_of_day: bool = False) -> tuple[int, bool]:
        """'YYYY-MM-DD', ISO datetime, 'today', 'tomorrow' or 'in N days' -> (epoch ms, has_time)."""
        raw = (value or "").strip().lower()
        today = datetime.now(self.tz).date()
        match = re.fullmatch(r"in (\d{1,3}) days?", raw)
        relative = {"today": 0, "tomorrow": 1, "yesterday": -1}
        if raw in relative:
            day = today + timedelta(days=relative[raw])
        elif match:
            day = today + timedelta(days=int(match.group(1)))
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            day = date.fromisoformat(raw)
        else:
            try:
                dt = datetime.fromisoformat(value.strip())
            except ValueError:
                raise ValueError(f"Invalid date {value!r}: use YYYY-MM-DD, ISO-8601, today, "
                                 "tomorrow or 'in N days'") from None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.tz)
            return int(dt.timestamp() * 1000), True
        moment = dtime(23, 59, 59) if end_of_day else dtime(0, 0)
        return int(datetime.combine(day, moment, tzinfo=self.tz).timestamp() * 1000), False

    # ------------------------------------------------------------------ hierarchy

    def hierarchy(self, account: str, *, refresh: bool = False) -> dict:
        """Spaces -> folders -> lists of the Workspace, cached 5 min per account."""
        with self._lock:
            cached = self._hierarchy_cache.get(account)
        if cached and not refresh and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        ws = self.config.workspace_id
        archived = {"archived": "false"}
        spaces = []
        for sp in self.request(account, "GET", f"/team/{ws}/space", params=archived).get("spaces", []):
            space: dict[str, Any] = {"id": str(sp["id"]), "name": sp.get("name", ""), "folders": [], "lists": []}
            for fo in self.request(account, "GET", f"/space/{space['id']}/folder", params=archived).get("folders", []):
                space["folders"].append({
                    "id": str(fo["id"]), "name": fo.get("name", ""),
                    "lists": [{"id": str(li["id"]), "name": li.get("name", "")}
                              for li in fo.get("lists", []) if not li.get("archived")],
                })
            for li in self.request(account, "GET", f"/space/{space['id']}/list", params=archived).get("lists", []):
                space["lists"].append({"id": str(li["id"]), "name": li.get("name", "")})
            spaces.append(space)
        data = {"workspace_id": ws, "spaces": spaces}
        with self._lock:
            self._hierarchy_cache[account] = (time.monotonic(), data)
        return data

    def _all_lists(self, account: str) -> list[dict]:
        out = []
        for sp in self.hierarchy(account)["spaces"]:
            base = {"space": sp["name"], "space_id": sp["id"]}
            for li in sp["lists"]:
                out.append({**base, "id": li["id"], "name": li["name"], "folder": None,
                            "path": f"{sp['name']}/{li['name']}"})
            for fo in sp["folders"]:
                for li in fo["lists"]:
                    out.append({**base, "id": li["id"], "name": li["name"], "folder": fo["name"],
                                "path": f"{sp['name']}/{fo['name']}/{li['name']}"})
        return out

    def resolve_list(self, account: str, ref: str) -> dict:
        """List by ID, path 'Space/Folder/List' or 'Space/List', or unique name."""
        ref = (ref or "").strip()
        if not ref:
            raise ValueError("Give the list (ID, path 'Space/Folder/List' or name)")
        lists = self._all_lists(account)
        if ref.isdigit():
            known = next((li for li in lists if li["id"] == ref), None)
            return known or {"id": ref, "name": ref, "path": ref, "space": None, "space_id": None, "folder": None}
        key = _norm(ref)
        for attr in ("path", "name"):
            hits = [li for li in lists if _norm(li[attr]) == key]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise _ambiguous("List", ref, (f"{h['path']} ({h['id']})" for h in hits))
        # Partial path: "Space/List" for a list inside a folder, or suffix "Folder/List".
        hits = [li for li in lists if _norm(li["path"]).endswith("/" + key)
                or _norm(f"{li['space']}/{li['name']}") == key]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise _ambiguous("List", ref, (f"{h['path']} ({h['id']})" for h in hits))
        raise _not_found("list", ref, (li["path"] for li in lists))

    def resolve_space(self, account: str, ref: str) -> dict:
        ref = (ref or "").strip()
        spaces = self.hierarchy(account)["spaces"]
        hits = [sp for sp in spaces if sp["id"] == ref or _norm(sp["name"]) == _norm(ref)]
        if len(hits) == 1:
            return hits[0]
        if ref.isdigit():
            return {"id": ref, "name": ref}
        raise _not_found("space", ref, (sp["name"] for sp in spaces))

    # ------------------------------------------------------------------ people

    def me(self, account: str) -> dict:
        with self._lock:
            cached = self._me_cache.get(account)
        if cached:
            return cached
        user = self.request(account, "GET", "/user").get("user") or {}
        data = {"id": user.get("id"), "username": user.get("username"), "email": user.get("email")}
        with self._lock:
            self._me_cache[account] = data
        return data

    def members(self, account: str) -> list[dict]:
        with self._lock:
            cached = self._members_cache.get(account)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
        ws = self.config.workspace_id
        teams = self.request(account, "GET", "/team").get("teams", [])
        team = next((t for t in teams if str(t.get("id")) == ws), None)
        if team is None:
            raise ClickUpError(f"Account {account!r} has no access to workspace {ws}")
        out = [{"id": u["id"], "username": u.get("username") or "", "email": u.get("email") or ""}
               for u in ((m.get("user") or {}) for m in team.get("members", [])) if u.get("id") is not None]
        with self._lock:
            self._members_cache[account] = (time.monotonic(), out)
        return out

    def resolve_member(self, account: str, ref: Any) -> int:
        """Numeric ID, 'me', email or username (exact, or a unique partial match)."""
        if isinstance(ref, int) and not isinstance(ref, bool):
            return ref
        raw = str(ref or "").strip()
        if raw.isdigit():
            return int(raw)
        if raw.lower() == "me":
            return int(self.me(account)["id"])
        people = self.members(account)
        key = _norm(raw)
        exact = [p for p in people if _norm(p["email"]) == key or _norm(p["username"]) == key]
        if len(exact) == 1:
            return int(exact[0]["id"])
        partial = exact or [p for p in people if key and (key in _norm(p["username"]) or key in _norm(p["email"]))]
        if len(partial) == 1:
            return int(partial[0]["id"])
        labels = (f"{p['username']} <{p['email']}> ({p['id']})" for p in partial or people)
        if partial:
            raise _ambiguous("Person", raw, labels)
        raise _not_found("person", raw, labels)

    def whoami(self, account: str) -> dict:
        return {"account": account, "workspace_id": self.config.workspace_id, "user": self.me(account),
                "members": self.members(account)}

    # ------------------------------------------------------------------ task shaping

    def shape_task(self, t: dict, *, full: bool = False) -> dict:
        status = t.get("status") or {}
        priority = t.get("priority") or {}
        out = _compact({
            "id": t.get("id"),
            "custom_id": t.get("custom_id"),
            "name": t.get("name"),
            "status": status.get("status") if isinstance(status, dict) else status,
            "priority": priority.get("priority") if isinstance(priority, dict) else None,
            "assignees": [a.get("username") or a.get("email") or a.get("id") for a in t.get("assignees") or []],
            "due_date": self._ms_to_iso(t.get("due_date")),
            "list": (t.get("list") or {}).get("name"),
            "list_id": (t.get("list") or {}).get("id"),
            "tags": [tag.get("name") for tag in t.get("tags") or []],
            "parent": t.get("parent"),
            "url": t.get("url"),
        })
        if not full:
            return out
        description = t.get("markdown_description") or t.get("text_content") or t.get("description") or ""
        if len(description) > _MAX_DESCRIPTION_CHARS:
            description = description[:_MAX_DESCRIPTION_CHARS] + "\n[Description truncated]"
        return _compact({
            **out,
            "description": description,
            "folder": (t.get("folder") or {}).get("name"),
            "space_id": (t.get("space") or {}).get("id"),
            "start_date": self._ms_to_iso(t.get("start_date")),
            "date_created": self._ms_to_iso(t.get("date_created")),
            "date_updated": self._ms_to_iso(t.get("date_updated")),
            "date_closed": self._ms_to_iso(t.get("date_closed")),
            "creator": (t.get("creator") or {}).get("username"),
            "watchers": [w.get("username") for w in t.get("watchers") or []],
            "custom_fields": [_shape_field(f) for f in t.get("custom_fields") or []
                              if f.get("value") not in (None, "", [])],
            "checklists": _shape_checklists(t.get("checklists") or []),
            "subtasks": [self.shape_task(s) for s in t.get("subtasks") or []],
        })

    def list_statuses(self, account: str, list_id: str) -> list[str]:
        data = self.request(account, "GET", f"/list/{list_id}")
        return [s.get("status") for s in data.get("statuses") or [] if s.get("status")]

    def _field_value(self, fld: dict, value: Any) -> Any:
        options = (fld.get("type_config") or {}).get("options") or []
        kind = fld.get("type")
        if kind == "drop_down" and isinstance(value, str) and options:
            for opt in options:
                if opt.get("id") == value or _norm(opt.get("name", "")) == _norm(value):
                    return opt["id"]
            raise ValueError(f"Invalid option {value!r} for {fld.get('name')}: "
                             f"{', '.join(o.get('name', '') for o in options)}")
        if kind == "labels" and options:
            ids = []
            for v in value if isinstance(value, list) else [value]:
                opt = next((o for o in options if o.get("id") == v
                            or _norm(o.get("label") or o.get("name") or "") == _norm(str(v))), None)
                if opt is None:
                    raise ValueError(f"Invalid label {v!r} for {fld.get('name')}")
                ids.append(opt["id"])
            return ids
        if kind == "date" and isinstance(value, str):
            return self.parse_date(value)[0]
        return value

    def _custom_field_payload(self, account: str, list_id: str, custom_fields: dict) -> list[dict]:
        fields = self.request(account, "GET", f"/list/{list_id}/field").get("fields") or []
        out = []
        for ref, value in custom_fields.items():
            fld = _match_field(fields, ref)
            out.append({"id": fld["id"], "value": self._field_value(fld, value)})
        return out
