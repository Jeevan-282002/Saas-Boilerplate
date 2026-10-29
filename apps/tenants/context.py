from contextvars import ContextVar, Token

_current_tenant_alias: ContextVar[str | None] = ContextVar("current_tenant_alias", default=None)


def get_tenant_alias() -> str | None:
    return _current_tenant_alias.get()


def set_tenant_alias(alias: str) -> Token:
    return _current_tenant_alias.set(alias)


def clear_tenant_alias(token: Token) -> None:
    _current_tenant_alias.reset(token)
