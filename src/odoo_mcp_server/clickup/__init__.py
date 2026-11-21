"""Optional native ClickUp integration (enable with the "clickup" tool group)."""

from .client import ClickUpClient, ClickUpConfig, ClickUpError

__all__ = ["ClickUpClient", "ClickUpConfig", "ClickUpError", "build_clickup_client"]


def build_clickup_client(settings) -> ClickUpClient | None:  # type: ignore[no-untyped-def]
    """Build the client when the "clickup" group is enabled, else None."""
    if "clickup" not in settings.effective_tool_groups:
        return None
    return ClickUpClient(ClickUpConfig(
        accounts=dict(settings.clickup_accounts),
        workspace_id=(settings.clickup_workspace_id or "").strip(),
        expected_user_ids=dict(settings.clickup_account_user_ids),
        default_account=settings.clickup_default_account or None,
        timezone=settings.clickup_timezone,
    ))
