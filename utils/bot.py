import logging
import os
import time as _time
from asyncio import AbstractEventLoop, CancelledError, Task
from collections.abc import Coroutine
from datetime import time
from typing import Any
from zoneinfo import ZoneInfo

import discord
import psutil
from discord.ext import commands, tasks

from utils import wiki_operations
from utils.exception_reporter import ExceptionReporter

BOT_REPORTS_CHANNEL_ID = 1403711339355963443
LOCAL_TZ = ZoneInfo("Europe/Berlin")
MEMORY_INTERVAL_HOURS = 12  # must be 0 < h <= 24

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
        self.wikiops = wiki_operations.WikiOperations(self)
        self.exception_reporter: ExceptionReporter | None = None
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

    @tasks.loop(
        time=tuple(
            time(hour=(i * MEMORY_INTERVAL_HOURS) % 24, tzinfo=LOCAL_TZ) for i in range(24 // MEMORY_INTERVAL_HOURS)
        )
    )
    async def memory_reporter(self, channel: discord.TextChannel, process: psutil.Process) -> None:
        """Periodically report memory and CPU usage to the given channel."""
        mem_mb = process.memory_info().rss / 1024 / 1024
        total_mb = psutil.virtual_memory().total / 1024 / 1024
        cpu_percent = process.cpu_percent(interval=None)
        if channel:
            await channel.send(f"🖥 Memory: {mem_mb:.2f} MB / {total_mb:.0f} MB | CPU: {cpu_percent:.1f}%")

    def install_asyncio_handler(self, reporter: ExceptionReporter) -> None:
        """Install a custom exception handler for the bot event loop to report exceptions."""

        def handler(loop: AbstractEventLoop, context: dict) -> None:
            exception = context.get("exception")

            if exception is None:
                exception = RuntimeError(context["message"])

            loop.create_task(reporter.report(exception, context=context.get("message")))

        self.loop.set_exception_handler(handler)

    @tasks.loop(seconds=5)
    async def watchdog_ticker(self) -> None:
        """Update the watchdog timestamp every few seconds."""
        self.watchdog_last_tick = _time.time()

    def watchdog(self, interval: int = 5, timeout: int = 15) -> None:
        """Kill the process if the event loop appears frozen for too long."""
        while True:
            _time.sleep(interval)
            if _time.time() - self.watchdog_last_tick > timeout:
                self.logger.error("Bot appears frozen, killing the process...")
                os._exit(1)
