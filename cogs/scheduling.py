import asyncio
import inspect
import json
import logging
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, cast

import discord
from discord import option
from discord.ext import commands

from utils.bot_utils import LOCAL_TZ, create_embed, create_task_with_logging

logger = logging.getLogger(__name__)

EXAMPLE_DATE_FORMAT = (datetime.now(LOCAL_TZ) + timedelta(days=3)).strftime("%d.%m. %H:%M")


@dataclass
class ScheduledPostArgs:
    """Container for all fields needed to schedule a post or reminder."""

    author_id: int
    guild_id: int | None
    channel_id: int
    message_id: int
    channel_id_source: int
    content: str
    post_at: datetime
    attachment_paths: list[str]
    publish: bool
    task_id: str = ""
    is_reminder: bool = False


class RemindMeSelect(discord.ui.Select):
    """Select component offering common reminder times."""

    def __init__(self, bot: commands.Bot, message: discord.Message) -> None:
        """Initialize the select with predefined options."""
        self.bot = bot
        self.message = message
        options = [
            discord.SelectOption(label="in 1h"),
            discord.SelectOption(label="in 12h"),
            discord.SelectOption(label="in 24h"),
            discord.SelectOption(label="at 8:00"),
            discord.SelectOption(label="at 18:00"),
            discord.SelectOption(label="at 20:00"),
            discord.SelectOption(label="Other"),
        ]
        super().__init__(placeholder="Choose a time...", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle selection and create the corresponding reminder."""
        chosen = cast("str", self.values[0])

        if chosen == "Other":
            await interaction.response.send_modal(CustomRemindModal(self.bot, self.message))
            return

        now = datetime.now(LOCAL_TZ)
        if chosen.startswith("in "):
            remind_at = now + timedelta(hours=int(cast("re.Match[str]", re.search(r"\d+", chosen)).group()))
        elif chosen.startswith("at "):
            hour = int(chosen.split(" ")[1].split(":")[0])
            remind_at = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if remind_at < now:
                remind_at = remind_at + timedelta(days=1)
        else:
            remind_at = now + timedelta(minutes=1)

        args = ScheduledPostArgs(
            author_id=interaction.user.id,  # type: ignore[arg-type]
            guild_id=interaction.guild_id,
            channel_id=interaction.user.id,  # type: ignore[arg-type] # DM
            message_id=self.message.id,
            channel_id_source=interaction.channel_id,  # type: ignore[arg-type]
            content=(
                f"Here's your reminder about "
                f"https://discord.com/channels/{interaction.guild_id or '@me'}/"
                f"{interaction.channel_id}/{self.message.id}"
            ),
            post_at=remind_at,
            attachment_paths=[],
            publish=False,
            is_reminder=True,
        )

        task_id = await cast("Scheduling", self.bot.get_cog("Scheduling")).create_reminder(args)

        await interaction.response.send_message(
            embed=create_embed(
                description=f"`{task_id[1:]}`: Alright, I'll remind you about https://discord.com/channels/"
                f"{interaction.guild_id or '@me'}/{interaction.channel_id}/"
                f"{self.message.id} <t:{int(remind_at.timestamp())}:R>!",
                color=0x00FF00,
            ),
            ephemeral=True,
        )


class RemindSelectView(discord.ui.View):
    """View containing the reminder time selector for the 'Remind Me' command."""

    def __init__(self, bot: commands.Bot, message: discord.Message) -> None:
        """Initialize the view with a `RemindMeSelect` control."""
        super().__init__()
        self.add_item(RemindMeSelect(bot, message))


class CustomRemindModal(discord.ui.Modal):
    """Modal to capture a custom date/time for a reminder."""

    def __init__(self, bot: commands.Bot, message: discord.Message) -> None:
        """Initialize the modal with an input field for date/time."""
        super().__init__(title="Reminding you...")
        self.bot = bot
        self.message = message

        example_date = (datetime.now(LOCAL_TZ) + timedelta(minutes=5)).strftime("%d.%m. %H:%M")
        self.add_item(
            discord.ui.InputText(
                label="When do you want me to remind you?",
                placeholder=example_date,
                value=example_date,
                style=discord.InputTextStyle.short,
            )
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Validate the date, create the reminder, and respond."""
        date = self.children[0].value or ""
        scheduling = cast("Scheduling", self.bot.get_cog("Scheduling"))

        if (dt := await scheduling.validate_date_and_respond(date, interaction.response)) is None:
            return

        remind_at = dt.replace(second=0, microsecond=0)

        args = ScheduledPostArgs(
            author_id=interaction.user.id,  # type: ignore[arg-type]
            guild_id=interaction.guild_id,
            channel_id=interaction.user.id,  # type: ignore[arg-type] # DM
            message_id=self.message.id,
            channel_id_source=interaction.channel_id,  # type: ignore[arg-type]
            content=(
                f"Here's your reminder about "
                f"https://discord.com/channels/{interaction.guild_id or '@me'}/"
                f"{interaction.channel_id}/{self.message.id}"
            ),
            post_at=remind_at,
            attachment_paths=[],
            publish=False,
            is_reminder=True,
        )

        task_id = await scheduling.create_reminder(args)

        await interaction.response.send_message(
            embed=create_embed(
                description=f"`{task_id[1:]}`: Alright, I'll remind you about https://discord.com/channels/"
                f"{interaction.guild_id or '@me'}/{interaction.channel_id}/"
                f"{self.message.id} <t:{int(remind_at.timestamp())}:R>!",
                color=0x00FF00,
            ),
            ephemeral=True,
        )


class Scheduling(commands.Cog):
    """Schedules posts and manages reminders using background tasks."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize scheduling state and ensure attachment storage exists."""
        self.bot = bot
        self.scheduled_tasks: dict[str, asyncio.Task] = {}
        self.scheduled_posts: list[dict] = []

        self.scheduled_posts_file_path = "./scheduled_posts.json"
        Path("attachments").mkdir(parents=True, exist_ok=True)

    def _get_wait_seconds(self, dt: datetime) -> float:
        """Compute seconds to wait until `dt` in local timezone."""
        return (dt - datetime.now(LOCAL_TZ)).total_seconds()

    async def _schedule(
        self, what: Callable[..., Coroutine[Any, Any, None] | None], wait_seconds: float, task_id: str, *args: object
    ) -> None:
        """Sleep for the given time, then execute the task function."""
        await asyncio.sleep(wait_seconds)

        logger.info("Finished waiting, posting %s now.", task_id)
        result = what(task_id, *args)
        if inspect.isawaitable(result):
            await result

    async def _send_scheduled_message(
        self,
        task_id: str,
        channel: discord.TextChannel | discord.User,
        content: str,
        file_paths: list[str],
        *,
        publish: bool,
    ) -> None:
        """Send the scheduled message, optionally publishing it, then cleanup."""
        files = [discord.File(path) for path in file_paths if Path(path).exists()]
        try:
            message = await channel.send(content, files=files)
            if publish:
                await message.publish()
        finally:
            self._cleanup_schedule_remains(task_id, file_paths)

    def _get_post_by_id(self, post_id: str) -> dict | None:
        """Retrieve a scheduled post/reminder by its id, if present."""
        return next((d for d in self.scheduled_posts if d["id"] == post_id), None)

    def _validate_post(self, ctx: discord.ApplicationContext, post: dict | None) -> bool:
        """Check that the post belongs to the guild/author and is not a reminder."""
        return not (
            post is None
            or ctx.guild_id != post["guild_id"]
            or ctx.author.id != post["author_id"]
            or post["id"].startswith("-")
        )

    def _validate_reminder(self, ctx: discord.ApplicationContext, post: dict | None) -> bool:
        """Check that the item is a reminder belonging to the author."""
        return not (post is None or ctx.author.id != post["author_id"] or not post["id"].startswith("-"))

    async def validate_date_and_respond(self, date: str, response: discord.InteractionResponse) -> datetime | None:
        """Validate date string and respond with an error if invalid; returns a datetime on success."""
        try:
            date_parts = date.split(" ")
            if date_parts and date_parts[0] and not date_parts[0].endswith("."):
                date = date.replace(" ", ". ", 1)
            dt = datetime.strptime(date, "%d.%m. %H:%M")  # noqa: DTZ007
            now = datetime.now(LOCAL_TZ)
            dt = dt.replace(year=now.year, tzinfo=LOCAL_TZ)

            if dt <= now:
                dt = dt.replace(year=now.year + 1, tzinfo=LOCAL_TZ)
        except ValueError:
            example_date = (datetime.now(LOCAL_TZ) + timedelta(minutes=5)).strftime("%d.%m. %H:%M")
            await response.send_message(
                embed=create_embed(
                    description=f"Please use the correct format for the date: `{example_date}`", color=0xFF0000
                ),
                ephemeral=True,
            )
            return None

        return dt

    def _generate_task_id(self, now: datetime, *, is_reminder: bool) -> str:
        """Generate a unique task id; reminders receive a leading '-' prefix."""
        task_id = "-" + f"{now.timestamp():.6f}".split(".")[1]
        while (task_id in self.scheduled_tasks) or (task_id[1:] in self.scheduled_tasks):
            task_id = str(int(task_id) - 1).zfill(7)
        return (is_reminder and task_id) or task_id[1:]

    def _create_schedule_task(self, **kwargs: object) -> asyncio.Task:
        func = partial(self._send_scheduled_message, publish=kwargs["publish"])  # type: ignore[arg-type]
        return create_task_with_logging(
            self.bot.loop,
            self._schedule(
                func,
                cast("float", kwargs["wait_seconds"]),
                cast("str", kwargs["task_id"]),
                kwargs["channel"],
                kwargs["content"],
                kwargs["attachments"],
            ),
        )

    def _delete_finished_tasks(self) -> None:
        """Remove completed tasks from the internal registry."""
        self.scheduled_tasks = {k: t for k, t in self.scheduled_tasks.items() if not t.done()}

    def _persist_posts(self) -> None:
        """Persist scheduled posts/reminders to disk as JSON."""
        try:
            with Path(self.scheduled_posts_file_path).open("w") as file:
                json.dump(self.scheduled_posts, file, indent=4)
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to update scheduled posts file")

    def _cleanup_schedule_remains(self, task_id: str, file_paths: list[str]) -> None:
        """Delete attachments and remove the scheduled item after execution."""
        self.scheduled_posts = [post for post in self.scheduled_posts if post["id"] != task_id]
        self._delete_finished_tasks()

        for path in file_paths:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                logger.exception("Failed to delete file")

        self._persist_posts()

    async def load_scheduled_posts(self, posts: list) -> None:
        """Restore scheduled tasks from a list of persisted posts and schedule them."""
        for post in posts:
            wait_seconds = self._get_wait_seconds(datetime.fromisoformat(post["post_time"]))
            if wait_seconds <= 0:
                logger.info("Trying to schedule %s failed: Due date is in the past.", post["id"])
                continue

            if post["id"].startswith("-"):
                user = await self.bot.get_or_fetch_user(post["author_id"])
                channel = user
            else:
                channel = self.bot.get_channel(post["channel_id"])

            self.scheduled_tasks[post["id"]] = self._create_schedule_task(
                wait_seconds=wait_seconds,
                task_id=post["id"],
                channel=channel,
                content=post["content"],
                attachments=post["attachments"],
                publish=post["publish"],
            )

            self.scheduled_posts.append(post)

            logger.info("Scheduled %s for %s", post["id"], post["post_time"])

        self._persist_posts()

    async def create_scheduled_post(self, args: ScheduledPostArgs) -> str:
        """Create and schedule a message or reminder and return its id."""
        now = datetime.now(LOCAL_TZ)
        task_id = args.task_id or self._generate_task_id(now, is_reminder=args.is_reminder)

        self.scheduled_posts.append(
            {
                "id": task_id,
                "author_id": args.author_id,
                "guild_id": args.guild_id,
                "channel_id": args.channel_id,
                "message_id": args.message_id,
                "channel_id_source": args.channel_id_source,
                "content": args.content,
                "post_time": args.post_at.isoformat(),
                "attachments": args.attachment_paths,
                "publish": args.publish,
            }
        )

        self.scheduled_tasks[task_id] = self._create_schedule_task(
            wait_seconds=self._get_wait_seconds(args.post_at),
            task_id=task_id,
            channel=(args.is_reminder and await self.bot.get_or_fetch_user(args.author_id))
            or self.bot.get_channel(args.channel_id),
            content=args.content,
            attachments=args.attachment_paths,
            publish=args.publish,
        )

        self._persist_posts()

        if args.is_reminder:
            logger.info("Reminder %s set for %s", task_id, args.post_at.isoformat())
        else:
            logger.info("Scheduled %s for %s", task_id, args.post_at.isoformat())

        return task_id

    async def create_reminder(self, args: ScheduledPostArgs) -> str:
        """Convenience wrapper to schedule a user DM reminder; returns task id."""
        return await self.create_scheduled_post(args)

    @commands.slash_command(
        name="schedule_post", description="Schedules a Discord post (your last sent message in this channel)"
    )
    @option(
        "channel",
        description="Which channel to post the message in",
        input_type=discord.abc.GuildChannel,
        required=True,
    )
    @option(
        "date",
        description=f"The date and time when to post the message (format: {EXAMPLE_DATE_FORMAT})",
        input_type=str,
        required=True,
    )
    @option(
        "id",
        parameter_name="post_id",
        description="The id of the message you want to schedule (only necessary if automatic fetch failed)",
        input_type=str,
        required=False,
    )
    @option(
        "publish",
        description="Whether or not to publish the message after it was posted",
        input_type=bool,
        required=False,
        default=False,
    )
    @commands.guild_only()
    async def schedule_post(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.abc.GuildChannel,
        date: str,
        post_id: str,
        *,
        publish: bool,
    ) -> None:
        """Slash command to schedule a message to be posted at a specific time."""
        self._delete_finished_tasks()

        to_schedule = None

        if (dt := await self.validate_date_and_respond(date, ctx.response)) is None:
            return

        if post_id:
            try:
                to_schedule = await ctx.channel.fetch_message(int(post_id))
            except discord.NotFound:
                await ctx.respond(
                    embed=create_embed(
                        description="Couldn't find the message, please check the id you provided.", color=0xFF0000
                    )
                )
                return
            except Exception:
                await ctx.respond(
                    embed=create_embed(
                        description="Something went wrong fetching your message, please try again.", color=0xFF0000
                    )
                )
                logger.exception("Error fetching the message to schedule via id")
                return
        else:
            async for message in ctx.channel.history(limit=10):
                if message.author == ctx.author:
                    to_schedule = message
                    break

            if not to_schedule:
                await ctx.respond(
                    embed=create_embed(
                        description="Failed to fetch your message automatically, please provide the message id.",
                        color=0xFF0000,
                    )
                )
                return

        now = datetime.now(LOCAL_TZ)
        task_id = self._generate_task_id(now, is_reminder=False)

        attachment_paths = []
        for i, attachment in enumerate(cast("discord.Message", to_schedule).attachments):
            filename = f"attachments/{task_id}_{i}_{attachment.filename}"
            await attachment.save(filename)  # type: ignore[attr-defined]
            attachment_paths.append(filename)

        args = ScheduledPostArgs(
            author_id=ctx.author.id,
            guild_id=ctx.guild_id,
            channel_id=channel.id,
            message_id=to_schedule.id,
            channel_id_source=ctx.channel_id,
            content=to_schedule.content,
            post_at=dt,
            attachment_paths=attachment_paths,
            publish=publish,
            task_id=task_id,
            is_reminder=False,
        )

        await self.create_scheduled_post(args)

        await ctx.respond(
            embed=create_embed(
                description=(
                    f"`{task_id}`: Scheduled https://discord.com/channels/"
                    f"{ctx.guild_id}/{ctx.channel_id}/{to_schedule.id} for "
                    f"<t:{int(dt.timestamp())}:F> in <#{channel.id}>"
                    f"{' (\u2060:mega:\u2060)' if publish else ''}"
                ),
                color=0x00FF00,
            )
        )

    # TODO: handle too many scheduled posts
    @commands.slash_command(name="list_scheduled_posts", description="Lists all scheduled posts")
    @commands.guild_only()
    async def list_scheduled_posts(self, ctx: discord.ApplicationContext) -> None:
        """List scheduled posts for the current guild authored by the requester."""
        response = ""
        for post in self.scheduled_posts:
            if not self._validate_post(ctx, post):
                continue
            response += (
                f"- `{post['id']}`: https://discord.com/channels/"
                f"{post['guild_id']}/{post['channel_id_source']}/{post['message_id']} "
                f"<t:{int(datetime.fromisoformat(post['post_time']).timestamp())}:R> in <#{post['channel_id']}>"
                f"{' (\u2060:mega:\u2060)' if post['publish'] else ''}\n"
            )

        if len(response) == 0:
            await ctx.respond(embed=create_embed(description="No posts have been scheduled."))
        else:
            await ctx.respond(embed=create_embed(description=response))

    # TODO: handle too many scheduled reminders
    @commands.slash_command(name="list_reminders", description="Lists all your reminders")
    async def list_reminders(self, ctx: discord.ApplicationContext) -> None:
        """List reminders created by the requester."""
        response = ""
        for reminder in self.scheduled_posts:
            if not self._validate_reminder(ctx, reminder):
                continue
            response += (
                f"- `{reminder['id'][1:]}`: https://discord.com/channels/"
                f"{reminder.get('guild_id') or '@me'}/{reminder['channel_id_source']}/{reminder['message_id']} "
                f"<t:{int(datetime.fromisoformat(reminder['post_time']).timestamp())}:R>\n"
            )

        if len(response) == 0:
            await ctx.respond(embed=create_embed(description="You have no reminders."), ephemeral=True)
        else:
            await ctx.respond(embed=create_embed(description=response), ephemeral=True)

    @commands.slash_command(name="modify_scheduled_post", description="Modifies a scheduled post")
    @option(
        "id",
        parameter_name="post_id",
        description="The id used to reference the scheduled message",
        input_type=str,
        required=True,
    )
    @option(
        "channel",
        description="Which channel to post the message in",
        input_type=discord.abc.GuildChannel,
        required=False,
    )
    @option(
        "date",
        description="The date and time when to post the message (format: 15.1. 14:20)",
        input_type=str,
        required=False,
    )
    @option(
        "message_id",
        description="The id of the message you want to schedule (to update the content and/or attachments)",
        input_type=str,
        required=False,
    )
    @option(
        "publish",
        description="Whether or not to publish the message after it was posted",
        input_type=bool,
        required=False,
    )
    @commands.guild_only()
    async def modify_scheduled_post(  # noqa: PLR0913
        self,
        ctx: discord.ApplicationContext,
        post_id: str,
        channel: discord.abc.GuildChannel,
        date: str,
        message_id: str,
        *,
        publish: bool,
    ) -> None:
        """Modify attributes of a scheduled post by id."""
        post = self._get_post_by_id(post_id)

        if not post or not self._validate_post(ctx, post):
            await ctx.respond(embed=create_embed(description="Couldn't find post with this ID.", color=0xFF0000))
            return

        if not channel and not date and not message_id and not publish:
            await ctx.respond(
                embed=create_embed(description="Please specify at least one value to update.", color=0xFF0000)
            )
            return

        if date:
            if (dt := await self.validate_date_and_respond(date, ctx.response)) is None:
                return

            post["post_time"] = dt.isoformat()

        if channel:
            post["channel_id"] = channel.id

        if message_id:
            message = await ctx.channel.fetch_message(int(message_id))
            attachment_paths = []
            for i, attachment in enumerate(message.attachments):
                filename = f"attachments/{post_id}_{i}_{attachment.filename}"
                await attachment.save(filename)  # type: ignore[attr-defined]
                attachment_paths.append(filename)

            post["message_id"] = int(message_id)
            post["channel_id_source"] = ctx.channel_id
            post["content"] = message.content
            post["attachments"] = attachment_paths

        if publish is not None:
            post["publish"] = publish

        self.scheduled_tasks[post_id].cancel()

        self.scheduled_tasks[post_id] = self._create_schedule_task(
            wait_seconds=self._get_wait_seconds(datetime.fromisoformat(post["post_time"])),
            task_id=post_id,
            channel=channel or self.bot.get_channel(post["channel_id"]),
            content=post["content"],
            attachments=post["attachments"],
            publish=post["publish"],
        )

        self._persist_posts()

        await ctx.respond(
            embed=create_embed(
                description=(
                    f"`{post['id']}`: Scheduled https://discord.com/channels/"
                    f"{ctx.guild_id}/{post['channel_id_source']}/{post['message_id']} for "
                    f"<t:{int(datetime.fromisoformat(post['post_time']).timestamp())}:F> in <#{post['channel_id']}>"
                    f"{' (\u2060:mega:\u2060)' if post['publish'] else ''}"
                ),
                color=0x00FF00,
            )
        )

    @commands.slash_command(name="delete_scheduled_post", description="Deletes a scheduled post")
    @option(
        "id",
        parameter_name="post_id",
        description="The id used to reference the scheduled message",
        input_type=str,
        required=True,
    )
    @commands.guild_only()
    async def delete_scheduled_post(self, ctx: discord.ApplicationContext, post_id: str) -> None:
        """Delete a scheduled post by id."""
        post = self._get_post_by_id(post_id)
        if not post or not self._validate_post(ctx, post):
            await ctx.respond(embed=create_embed(description="Couldn't find post with this ID.", color=0xFF0000))
            return

        self.scheduled_tasks[post_id].cancel()
        self._cleanup_schedule_remains(post_id, post["attachments"])

        await ctx.respond(embed=create_embed(description="Deleted scheduled post successfully.", color=0x00FF00))

    @commands.slash_command(name="delete_reminder", description="Deletes a reminder")
    @option("id", parameter_name="reminder_id", description="The reminder's id", input_type=str, required=True)
    async def delete_reminder(self, ctx: discord.ApplicationContext, reminder_id: str) -> None:
        """Delete a scheduled reminder by id."""
        reminder_id = "-" + reminder_id
        reminder = self._get_post_by_id(reminder_id)

        if not reminder or not self._validate_reminder(ctx, reminder):
            await ctx.respond(embed=create_embed(description="Couldn't find reminder with this ID.", color=0xFF0000))
            return

        self.scheduled_tasks[reminder_id].cancel()
        self._cleanup_schedule_remains(reminder_id, reminder["attachments"])

        await ctx.respond(embed=create_embed(description="Deleted reminder successfully.", color=0x00FF00))

    @commands.message_command(name="Remind Me")
    async def remind_me(self, ctx: discord.ApplicationContext, message: discord.Message) -> None:
        """Message command to set a reminder for the selected message."""
        await ctx.respond(
            "Remind me about this message:", view=RemindSelectView(self.bot, message), ephemeral=True, delete_after=10
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """In DMs, offer a quick reminder UI for new messages."""
        if message.author.bot or message.guild:
            return

        await message.reply(
            "Remind me about this message:",
            view=RemindSelectView(self.bot, message),
            delete_after=10,
            mention_author=False,
        )


def setup(bot: commands.Bot) -> None:
    """Register the `Scheduling` cog with the bot."""
    bot.add_cog(Scheduling(bot))
