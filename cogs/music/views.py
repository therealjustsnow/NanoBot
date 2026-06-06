"""Discord UI views for the music player (control panel, paginators, search)."""

import math
from typing import Optional, TYPE_CHECKING

import discord
from discord.ext import commands

from utils import helpers as h

from .constants import (
    ACCENT,
    LOOP_OFF,
    _LOOP_NEXT,
    _LOOP_LABEL,
    PAGE_SIZE_QUEUE,
    PAGE_SIZE_APL,
)
from .helpers import _fmt_time, _ellipsize
from .track import Track

if TYPE_CHECKING:
    from .player import GuildPlayer
    from .cog import Music


class Controls(discord.ui.View):
    """Interactive buttons attached to the Now Playing card."""

    def __init__(self, player: "GuildPlayer"):
        super().__init__(timeout=None)
        self.player = player
        self._sync()

    def _sync(self) -> None:
        """Reflect current playback state on the buttons."""
        paused = bool(self.player.voice and self.player.voice.is_paused())
        self.toggle.emoji = "▶️" if paused else "⏸️"
        self.toggle.label = "Resume" if paused else "Pause"
        self.loop_btn.label = _LOOP_LABEL[self.player.loop]
        self.loop_btn.style = (
            discord.ButtonStyle.success
            if self.player.loop != LOOP_OFF
            else discord.ButtonStyle.secondary
        )
        self.autoplay_btn.style = (
            discord.ButtonStyle.success
            if self.player.autoplay
            else discord.ButtonStyle.secondary
        )
        self.guildplay_btn.style = (
            discord.ButtonStyle.success
            if self.player.guildplay
            else discord.ButtonStyle.secondary
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        voice = self.player.voice
        user_vc = getattr(interaction.user.voice, "channel", None)
        if not voice or not voice.channel:
            await interaction.response.send_message(
                "I'm not connected to a voice channel.", ephemeral=True
            )
            return False
        if user_vc != voice.channel:
            await interaction.response.send_message(
                "Join my voice channel to use these controls.", ephemeral=True
            )
            return False
        return True

    async def _update_controls(self, interaction: discord.Interaction) -> None:
        self._sync()
        try:
            await interaction.response.edit_message(
                embed=self.player.now_playing_embed(), view=self
            )
        except discord.HTTPException:
            pass

    # Row 0 — transport
    @discord.ui.button(
        emoji="⏸️", label="Pause", style=discord.ButtonStyle.primary, row=0
    )
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button):
        voice = self.player.voice
        if voice and voice.is_paused():
            self.player.resume()
        elif voice and voice.is_playing():
            self.player.pause()
        await self._update_controls(interaction)

    @discord.ui.button(
        emoji="⏭️", label="Skip", style=discord.ButtonStyle.secondary, row=0
    )
    async def skip(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.player.skip()
        await interaction.response.send_message("⏭️ Skipped.", ephemeral=True)

    @discord.ui.button(
        emoji="⏹️", label="Stop", style=discord.ButtonStyle.danger, row=0
    )
    async def stop(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_message(
            "⏹️ Stopped and left the channel.", ephemeral=True
        )
        await self.player.destroy(reason="stop button")

    @discord.ui.button(
        label="Off", emoji="🔁", style=discord.ButtonStyle.secondary, row=0
    )
    async def loop_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.player.loop = _LOOP_NEXT[self.player.loop]
        self.player._schedule_save()
        await self._update_controls(interaction)

    @discord.ui.button(
        emoji="🔀", label="Shuffle", style=discord.ButtonStyle.secondary, row=0
    )
    async def shuffle(self, interaction: discord.Interaction, _: discord.ui.Button):
        n = self.player.shuffle()
        await interaction.response.send_message(
            f"🔀 Shuffled **{n}** track(s).", ephemeral=True
        )

    # Row 1 — extras
    @discord.ui.button(
        emoji="⏮️", label="Replay", style=discord.ButtonStyle.secondary, row=1
    )
    async def replay(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.player.seek(0)
        await interaction.response.send_message("⏮️ Replaying.", ephemeral=True)

    @discord.ui.button(
        emoji="✨", label="Autoplay", style=discord.ButtonStyle.secondary, row=1
    )
    async def autoplay_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        self.player.autoplay = not self.player.autoplay
        self.player._added.set()
        await self._update_controls(interaction)

    @discord.ui.button(
        emoji="📻", label="Guild Play", style=discord.ButtonStyle.secondary, row=1
    )
    async def guildplay_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        self.player.guildplay = not self.player.guildplay
        self.player._added.set()
        await self._update_controls(interaction)

    @discord.ui.button(
        emoji="📜", label="Queue", style=discord.ButtonStyle.secondary, row=1
    )
    async def queue_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        needs_pages = len(self.player.queue) > PAGE_SIZE_QUEUE
        view = QueuePageView(self.player) if needs_pages else None
        await interaction.response.send_message(
            embed=self.player.queue_embed(0), view=view, ephemeral=True
        )
        if needs_pages:
            view.message = await interaction.original_response()


# ── Paginated queue view ────────────────────────────────────────────────────────
class QueuePageView(discord.ui.View):
    """Scrollable queue display; reads live from the player on each page turn."""

    def __init__(self, player: "GuildPlayer"):
        super().__init__(timeout=120)
        self.player = player
        self.page = 0
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def _total_pages(self) -> int:
        return max(1, math.ceil(len(self.player.queue) / PAGE_SIZE_QUEUE))

    def _sync_buttons(self) -> None:
        tp = self._total_pages()
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= tp - 1
        self.page_indicator.label = f"{self.page + 1}/{tp}"

    async def on_timeout(self) -> None:
        if self.message:
            try:
                for item in self.children:
                    item.disabled = True
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.player.queue_embed(self.page), view=self
        )

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_indicator(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.defer()

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.player.queue_embed(self.page), view=self
        )


# ── Paginated guild playlist view ───────────────────────────────────────────────
class AplPageView(discord.ui.View):
    """Scrollable guild playlist display; snapshot of entries at creation time."""

    def __init__(self, entries: list, invoker_id: int):
        super().__init__(timeout=120)
        self.entries = entries
        self.invoker_id = invoker_id
        self.page = 0
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def _total_pages(self) -> int:
        return max(1, math.ceil(len(self.entries) / PAGE_SIZE_APL))

    def build_embed(self) -> discord.Embed:
        start = self.page * PAGE_SIZE_APL
        end = start + PAGE_SIZE_APL
        lines = []
        for i, e in enumerate(self.entries[start:end], start=start + 1):
            label = e["title"] or e["url"]
            if len(label) > 60:
                label = label[:57] + "…"
            lines.append(f"`{i:>2}.` [{label}]({e['url']})")
        tp = self._total_pages()
        embed = h.embed(
            f"📻 Guild Playlist · {len(self.entries)} track(s)",
            "\n".join(lines),
            ACCENT,
        )
        if tp > 1:
            embed.set_footer(text=f"Page {self.page + 1}/{tp}")
        return embed

    def _sync_buttons(self) -> None:
        tp = self._total_pages()
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= tp - 1
        self.page_indicator.label = f"{self.page + 1}/{tp}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran this command can scroll.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message:
            try:
                for item in self.children:
                    item.disabled = True
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_indicator(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.defer()

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


# ── Search result picker ────────────────────────────────────────────────────────
class SearchView(discord.ui.View):
    """A dropdown of search results; the invoker picks one to queue."""

    def __init__(self, cog: "Music", ctx: commands.Context, tracks: list[Track]):
        super().__init__(timeout=60)
        self.cog = cog
        self.ctx = ctx
        self.tracks = tracks
        self.message: Optional[discord.Message] = None

        options = []
        for i, t in enumerate(tracks):
            label = _ellipsize(t.title, 90)
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(i),
                    description=_fmt_time(t.duration),
                    emoji="🎵",
                )
            )
        self.select.options = options

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Only the person who searched can pick.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(
                    content="⌛ Search timed out.", view=None, embed=None
                )
            except discord.HTTPException:
                pass

    @discord.ui.select(placeholder="Pick a track to queue…")
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select):
        track = self.tracks[int(select.values[0])]
        player = await self.cog._ensure_voice(self.ctx, join=True)
        if player is None:
            await interaction.response.edit_message(
                content="Couldn't join your voice channel.", view=None, embed=None
            )
            return
        was_idle = player.current is None and not player.queue
        player.add(track)
        verb = "Now playing" if was_idle else "Queued"
        await interaction.response.edit_message(
            content=None,
            embed=h.ok(
                f"**[{track.title}]({track.webpage_url})**\n`{_fmt_time(track.duration)}`",
                f"🎵 {verb}",
            ),
            view=None,
        )
        self.stop()


def _apl_single_embed(entries: list) -> discord.Embed:
    lines = []
    for i, e in enumerate(entries, start=1):
        label = e["title"] or e["url"]
        if len(label) > 60:
            label = label[:57] + "…"
        lines.append(f"`{i:>2}.` [{label}]({e['url']})")
    return h.embed(
        f"📻 Guild Playlist · {len(entries)} track(s)", "\n".join(lines), ACCENT
    )
