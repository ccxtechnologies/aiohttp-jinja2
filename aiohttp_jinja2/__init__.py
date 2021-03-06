import asyncio
import functools
import warnings
import jinja2
from collections.abc import Mapping
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, cast
from aiohttp import web
from aiohttp.abc import AbstractView
from .helpers import GLOBAL_HELPERS

__version__ = '1.2.0.a'

__all__ = ('setup', 'get_env', 'render_template', 'render_string', 'template')

APP_CONTEXT_PROCESSORS_KEY = 'aiohttp_jinja2_context_processors'
APP_KEY = 'aiohttp_jinja2_environment'
REQUEST_CONTEXT_KEY = 'aiohttp_jinja2_context'


def setup(
        app: web.Application,
        *args: Any,
        app_key: str = APP_KEY,
        context_processors: Iterable[Callable[[web.Request], Dict[str,
                                                                  Any]]] = (),
        filters: Optional[Iterable[Callable[..., str]]] = None,
        default_helpers: bool = True,
        enable_async: bool = True,
        **kwargs: Any
) -> jinja2.Environment:
    kwargs.setdefault("autoescape", True)
    env = jinja2.Environment(*args, enable_async=enable_async, **kwargs)
    if default_helpers:
        env.globals.update(GLOBAL_HELPERS)
    if filters is not None:
        env.filters.update(filters)
    app[app_key] = env
    if context_processors:
        app[APP_CONTEXT_PROCESSORS_KEY] = context_processors
        app.middlewares.append(context_processors_middleware)

    env.globals['app'] = app

    return env


def get_env(
        app: web.Application, *, app_key: str = APP_KEY
) -> jinja2.Environment:
    return cast(jinja2.Environment, app.get(app_key))


@asyncio.coroutine
def render_string(
        template_name: str,
        request: web.Request,
        context: Dict[str, Any],
        *,
        app_key: str = APP_KEY
) -> str:
    env = request.config_dict.get(app_key)
    if env is None:
        text = (
                "Template engine is not initialized, "
                "call aiohttp_jinja2.setup(..., app_key={}) first"
                "".format(app_key)
        )
        # in order to see meaningful exception message both: on console
        # output and rendered page we add same message to *reason* and
        # *text* arguments.
        raise web.HTTPInternalServerError(reason=text, text=text)
    try:
        template = env.get_template(template_name)
    except jinja2.TemplateNotFound as e:
        text = "Template '{}' not found".format(template_name)
        raise web.HTTPInternalServerError(reason=text, text=text) from e
    if not isinstance(context, Mapping):
        text = "context should be mapping, not {}".format(type(context))
        # same reason as above
        raise web.HTTPInternalServerError(reason=text, text=text)
    if request.get(REQUEST_CONTEXT_KEY):
        context = dict(request[REQUEST_CONTEXT_KEY], **context)
    if env.is_async:
        text = yield from template.render_async(context)
    else:
        text = template.render(context)
    return text


@asyncio.coroutine
def render_template(
        template_name,
        request,
        context,
        *,
        app_key=APP_KEY,
        encoding='utf-8',
        status=200
):
    response = web.Response(status=status)
    if context is None:
        context = {}
    text = yield from render_string(
            template_name, request, context, app_key=app_key
    )
    response.content_type = 'text/html'
    response.charset = encoding
    response.text = text
    return response


def template(
        template_name: str,
        *,
        app_key: str = APP_KEY,
        encoding: str = 'utf-8',
        status: int = 200
) -> Any:
    def wrapper(func: Any) -> Any:
        @asyncio.coroutine
        @functools.wraps(func)
        def wrapped(*args: Any) -> web.StreamResponse:
            if asyncio.iscoroutinefunction(func):
                coro = func
            else:
                warnings.warn(
                        "Bare functions are deprecated, "
                        "use async ones", DeprecationWarning
                )
                coro = asyncio.coroutine(func)
            context = yield from coro(*args)
            if isinstance(context, web.StreamResponse):
                return context

            # Supports class based views see web.View
            if isinstance(args[0], AbstractView):
                request = args[0].request
            else:
                request = args[-1]

            response = yield from render_template(
                    template_name,
                    request,
                    context,
                    app_key=app_key,
                    encoding=encoding
            )
            response.set_status(status)
            return response

        return wrapped

    return wrapper


@web.middleware
async def context_processors_middleware(
        request: web.Request, handler: Callable[[web.Request],
                                                Awaitable[web.StreamResponse]]
) -> web.StreamResponse:

    if REQUEST_CONTEXT_KEY not in request:
        request[REQUEST_CONTEXT_KEY] = {}
    for processor in request.config_dict[APP_CONTEXT_PROCESSORS_KEY]:
        request[REQUEST_CONTEXT_KEY].update(await processor(request))
    return await handler(request)


async def request_processor(request: web.Request) -> Dict[str, web.Request]:
    return {'request': request}
