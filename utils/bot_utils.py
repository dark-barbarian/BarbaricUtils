import logging
from asyncio import AbstractEventLoop, CancelledError, Task
from collections.abc import Coroutine
from typing import Any
from zoneinfo import ZoneInfo

import discord

LOCAL_TZ = ZoneInfo("Europe/Berlin")

logger = logging.getLogger(__name__)


def create_embed(
    title: str | None = None,
    description: str | None = None,
    color: int | None = None,
    footer: str | None = None,
) -> discord.Embed:
    """Create a standardized Discord embed used across the bot."""
    embed_var = discord.Embed(title=title, description=description, color=color)
    embed_var.set_footer(text=footer)
    return embed_var


def report_task_exception(task: Task) -> None:
    """Log exceptions for background tasks with consistent formatting."""
    try:
        if exc := task.exception():
            logger.error("A task threw an exception: %r", exc)
    except CancelledError:
        logger.info("A task was cancelled.")


def create_task_with_logging(loop: AbstractEventLoop, coro: Coroutine[Any, Any, None]) -> Task:
    """Create a task and attach an exception reporter callback."""
    task = loop.create_task(coro)
    task.add_done_callback(report_task_exception)
    return task
