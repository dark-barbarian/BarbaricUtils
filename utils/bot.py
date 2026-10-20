import logging
import os
import threading
import time as _time
from asyncio import AbstractEventLoop, CancelledError, Task
from collections.abc import Coroutine
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
import psutil
from discord.ext import commands, tasks

from utils import wiki_operations
from utils.exception_reporter import ExceptionReporter

BOT_REPORTS_CHANNEL_ID = 1403711339355963443
WATCHDOG_REPORT_PATH = Path("./persistent/watchdog_report.pending")
WATCHDOG_TIMEOUT = 15
WATCHDOG_POLL_INTERVAL = 5
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

            loop.create_task(
                reporter.report(exception, error_message=context.get("message") or "", context=context.get("message"))
            )

        self.loop.set_exception_handler(handler)

    @tasks.loop(seconds=WATCHDOG_POLL_INTERVAL)
    async def watchdog_ticker(self) -> None:
        """Update the watchdog timestamp every few seconds."""
        self.watchdog_last_tick = _time.monotonic()

    def watchdog(self, interval: int = WATCHDOG_POLL_INTERVAL, timeout: int = WATCHDOG_TIMEOUT) -> None:
        """Kill the process if the event loop is blocked for too long."""
        wait_event = threading.Event()
        while True:
            wait_event.wait(interval)
            heartbeat_age = _time.monotonic() - self.watchdog_last_tick
            if heartbeat_age > timeout:
                self.logger.error(
                    "Bot appears frozen (no heartbeat for %.1f seconds; timeout is %d), killing the process...",
                    heartbeat_age,
                    timeout,
                )
                try:
                    self._persist_watchdog_report()
                except OSError:
                    self.logger.exception("Unable to persist watchdog alert for the next startup")
                finally:
                    os._exit(1)

    @staticmethod
    def _persist_watchdog_report() -> None:
        """Save a pending watchdog report atomically for delivery after restart."""
        WATCHDOG_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = WATCHDOG_REPORT_PATH.with_suffix(".tmp")
        temporary_path.write_text(str(int(_time.time())))
        temporary_path.replace(WATCHDOG_REPORT_PATH)

    async def report_pending_watchdog(self, reporter: ExceptionReporter) -> None:
        """Send and clear a watchdog report left by the previous process."""
        try:
            timestamp = WATCHDOG_REPORT_PATH.read_text()
            detected_at = int(timestamp)
            await reporter.report(
                None,
                error_message="The bot event loop stopped responding.",
                context=f"The previous bot process was shut down by the watchdog.\nDetected at <t:{detected_at}:F>.",
                include_traceback=False,
            )
            WATCHDOG_REPORT_PATH.unlink()
        except FileNotFoundError:
            return
        except (discord.DiscordException, OSError):
            self.logger.exception("Unable to send or clear the pending watchdog report")
