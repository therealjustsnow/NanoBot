"""Stateless helpers for the moderation cog: target resolution, DM delivery,
role-hierarchy checks, the action-log embed, and chunked sleeping."""

import asyncio

import discord

from utils import helpers as h


def resolve_target(bot, channel_id, explicit):
    return explicit if explicit else bot.last_senders.get(channel_id)


async def try_dm(member, content):
    try:
        await member.send(content)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def can_target(actor, target):
    if actor == actor.guild.owner:
        return True
    return actor.top_role > target.top_role


def can_bot_target(bot_member, target):
    """Check whether the bot's role is high enough to act on target.

    This is separate from can_target (which checks the human moderator).
    Discord will 403 if the bot's top role is not strictly above the target's
    top role, even when the bot has the relevant permission node.
    """
    return bot_member.top_role > target.top_role


async def action_log(ctx, emoji, action, *, target=None, detail=""):
    if target is not None:
        e = h.mod_action_embed(
            f"{emoji} {action.title()}",
            target,
            detail or "No reason given",
            moderator=ctx.author,
            color=h.GREY,
        )
    else:
        desc = f"{emoji} **{ctx.author.display_name}** used **{action}**"
        if detail:
            desc += f"\n{detail}"
        e = discord.Embed(description=desc, color=h.GREY)
        e.timestamp = discord.utils.utcnow()
        e.set_footer(text="NanoBot")
    try:
        await ctx.channel.send(embed=e)
    except discord.HTTPException:
        pass


async def _chunked_sleep(seconds: float) -> None:
    """Sleep in <=1h chunks. A single multi-month asyncio.sleep is fragile;
    chunking keeps each await short."""
    remaining = seconds
    chunk = 3600
    while remaining > 0:
        await asyncio.sleep(min(remaining, chunk))
        remaining -= chunk
