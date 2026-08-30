"""Stand-in for ``astrbot.api.event.filter`` (command decorators)."""


class PermissionType:
    ADMIN = "admin"
    GROUP_ADMIN = "group_admin"


class _CommandGroup:
    def __init__(self, name):
        self.name = name

    def __call__(self, func):
        # `@filter.command_group("sf")` decorates the function; the name
        # `sf` must keep supporting `@sf.command(...)`, so return self.
        return self

    def command(self, name):
        def decorator(func):
            return func

        return decorator


def command_group(name):
    return _CommandGroup(name)


def permission_type(perm):
    def decorator(func):
        return func

    return decorator


def on_llm_request(*args, **kwargs):
    def decorator(func):
        return func

    return decorator
