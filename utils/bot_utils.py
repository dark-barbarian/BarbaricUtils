from asyncio import AbstractEventLoop, CancelledError, Task
import logging
from typing import Any, Coroutine
from zoneinfo import ZoneInfo
import discord


local_tz = ZoneInfo("Europe/Berlin")
        
def create_embed(title=None, description=None, color=None, footer=None):
    embed_var = discord.Embed(title=title, description=description, color=color)
    embed_var.set_footer(text=footer)
    return embed_var

def report_task_exception(task: Task):
    try:
        if exc := task.exception():
            logging.error(f"A task threw an exception: {exc!r}")
    except CancelledError:
        logging.info("A task was cancelled.")

def create_task_with_logging(loop: AbstractEventLoop, coro: Coroutine[Any, Any, None]):
    task = loop.create_task(coro)
    task.add_done_callback(report_task_exception)
    return task