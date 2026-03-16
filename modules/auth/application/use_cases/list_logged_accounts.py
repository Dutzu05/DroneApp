from __future__ import annotations


def list_logged_accounts(*, login_audit_repo):
    return login_audit_repo.list_accounts()
