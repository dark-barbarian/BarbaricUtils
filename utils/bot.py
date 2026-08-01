import logging
from asyncio import AbstractEventLoop, CancelledError, Task
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from utils import wiki_operations

if TYPE_CHECKING:
    from utils.exception_reporter import ExceptionReporter


LOCAL_TZ = ZoneInfo("Europe/Berlin")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s]: %(message)s",
    handlers=[logging.FileHandler("barbaricutils.log"), logging.StreamHandler()],
)


class Bot(commands.Bot):
    """Custom discord.ext.commands.Bot class to hold additional attributes."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.watchdog_last_tick = 0.0
        self.wikiops = wiki_operations.WikiOperations()
        self.reporter: ExceptionReporter | None = None
        self.logger = logging.getLogger(__name__)

    def create_embed(
        self,
        title: str | None = None,
        description: str | None = None,
        color: int | discord.Colour | None = None,
        footer: str | None = None,
    ) -> discord.Embed:
        """Create a standardized Discord embed used across the bot."""
        embed_var = discord.Embed(title=title, description=description, color=color)
        embed_var.set_footer(text=footer)
        return embed_var

    def report_task_exception(self, task: Task) -> None:
        """Log exceptions for background tasks with consistent formatting."""
        try:
            if exc := task.exception():
                self.logger.error("A task threw an exception: %r", exc)
        except CancelledError:
            self.logger.info("A task was cancelled.")

    def create_task_with_logging(self, loop: AbstractEventLoop, coro: Coroutine[Any, Any, None]) -> Task:
        """Create a task and attach an exception reporter callback."""
        task = loop.create_task(coro)
        task.add_done_callback(self.report_task_exception)
        return task
