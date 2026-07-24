"""
Command-level tests for cogs/casino/ under dpytest (parse → check → DB → reply).
"""

import time
import types

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from tests.conftest import config, grant_perms


class _FakeResponse:
    """Stand-in for discord.InteractionResponse: records what was sent/edited
    without touching the gateway — the view's callbacks only ever call
    edit_message/send_message on this."""

    def __init__(self):
        self.edited = None
        self.sent = None

    async def edit_message(self, **kwargs):
        self.edited = kwargs

    async def send_message(self, **kwargs):
        self.sent = kwargs


class _FakeInteraction:
    """Minimal duck-typed discord.Interaction for driving BlackjackView button
    handlers directly (dpytest has no component-interaction dispatch)."""

    def __init__(self, user_id: int):
        self.user = types.SimpleNamespace(id=user_id)
        self.response = _FakeResponse()


@pytest.mark.cogs("cogs.casino")
async def test_flip_win_credits_payout_and_streak(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)
    # roll 0.0 -> "heads" always wins.
    monkeypatch.setattr(casino.random, "random", lambda: 0.0)
    # Isolate this balance assertion from the (guild, user, day)-seeded daily
    # challenges — see test_challenge_* below for that system's own coverage.
    monkeypatch.setattr(casino, "generate_challenges", lambda *a, **k: [])

    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "Won" in sent.embeds[0].title
    # 500 - 100 (debit) + round(100*1.92) (payout) = 592
    assert await db.get_balance(author.id) == 592
    stats = await db.get_casino_stats(author.id)
    assert stats["games"] == 1
    assert stats["streak"] == 1


@pytest.mark.cogs("cogs.casino")
async def test_flip_loss_debits_and_resets_streak(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)
    # roll near 1.0 -> "tails"; betting on heads loses.
    monkeypatch.setattr(casino.random, "random", lambda: 0.999999)
    monkeypatch.setattr(casino, "generate_challenges", lambda *a, **k: [])

    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "Lost" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 400
    stats = await db.get_casino_stats(author.id)
    assert stats["streak"] == 0


@pytest.mark.cogs("cogs.casino")
async def test_flip_insufficient_funds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 10)

    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 10
    assert (await db.get_casino_stats(author.id))["games"] == 0


@pytest.mark.cogs("cogs.casino")
async def test_flip_invalid_side(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)

    await dpytest.message("!casino flip 100 sideways", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 500


@pytest.mark.cogs("cogs.casino")
async def test_bet_below_minimum_rejected(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)

    await dpytest.message("!casino flip 1 heads", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert "between" in sent.embeds[0].description
    assert await db.get_balance(author.id) == 500


@pytest.mark.cogs("cogs.casino")
async def test_bet_zero_or_negative_rejected(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)

    await dpytest.message("!casino dice 0", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 500


@pytest.mark.cogs("cogs.casino")
async def test_dice_push_refunds_bet(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)
    # Both player and dealer roll a total of 7 (die(0.0)=1, die(0.999)=6).
    rolls = iter([0.0, 0.999999, 0.999999, 0.0])
    monkeypatch.setattr(casino.random, "random", lambda: next(rolls))
    monkeypatch.setattr(casino, "generate_challenges", lambda *a, **k: [])

    await dpytest.message("!casino dice 100", member=author)
    sent = dpytest.get_message()
    assert "Push" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 500  # bet refunded


@pytest.mark.cogs("cogs.casino")
async def test_slots_triple_seven_awards_jackpot(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 1000)
    await db.add_to_jackpot(guild.id, 777)
    monkeypatch.setattr(casino.random, "random", lambda: 0.999999)  # all reels -> 7️⃣
    monkeypatch.setattr(casino, "generate_challenges", lambda *a, **k: [])

    await dpytest.message("!casino slots 100", member=author)
    sent = dpytest.get_message()
    assert "Jackpot" in sent.embeds[0].title
    assert any("JACKPOT" in (f.name or "") for f in sent.embeds[0].fields)
    assert (await db.get_casino_config(guild.id))["jackpot_pool"] == 0
    # 1000 - 100 (debit) + 4500 (triple-7 payout) + 777 (jackpot) = 6177
    assert await db.get_balance(author.id) == 6177


@pytest.mark.cogs("cogs.casino")
async def test_roulette_unknown_space_rejected(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)

    await dpytest.message("!casino roulette 100 purple", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 500


@pytest.mark.cogs("cogs.casino")
async def test_roulette_number_bet_wins(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)
    monkeypatch.setattr(casino.random, "random", lambda: 0.0)  # spins to pocket 0
    monkeypatch.setattr(casino, "generate_challenges", lambda *a, **k: [])

    await dpytest.message("!casino roulette 100 0", member=author)
    sent = dpytest.get_message()
    assert "Won" in sent.embeds[0].title
    # 500 - 100 + 100*35 = 3900
    assert await db.get_balance(author.id) == 3900


@pytest.mark.cogs("cogs.casino")
async def test_blackjack_deals_and_debits(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)

    # A fixed, non-shuffled shoe (shoe.pop() takes from the end): dealt order
    # is player 2♠ 7♥ (9, no natural), dealer 3♦ 4♣ (7, no natural).
    fixed_shoe = [("4", "♣"), ("3", "♦"), ("7", "♥"), ("2", "♠")]
    monkeypatch.setattr(casino, "new_shoe", lambda decks: list(fixed_shoe))
    monkeypatch.setattr(casino.random, "shuffle", lambda seq: None)

    await dpytest.message("!casino blackjack 100", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert sent.embeds[0].title == "🃏 Blackjack"
    assert await db.get_balance(author.id) == 400  # bet debited, hand open

    # The open hand is persisted (restart-safety) the moment it's dealt.
    open_hands = await db.get_open_blackjack_hands()
    assert len(open_hands) == 1
    assert open_hands[0]["bet"] == 100
    assert open_hands[0]["user_id"] == author.id
    assert open_hands[0]["message_id"] == sent.id

    casino_cog = bot.get_cog("Casino")
    hand_id = open_hands[0]["hand_id"]
    assert hand_id in casino_cog._bj_hands
    assert hand_id in casino_cog._bj_tasks


@pytest.mark.cogs("cogs.casino")
async def test_blackjack_hit_persists_shoe_and_hand(bot, monkeypatch):
    """deal → hit persists shoe (the row mirrors every in-memory hit)."""
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)

    # Dealt (popped from the end): player 2♠ 7♥ (9), dealer 3♦ 4♣ (7) — no
    # naturals. Next Hit pops 5♣.
    fixed_shoe = [("5", "♣"), ("4", "♣"), ("3", "♦"), ("7", "♥"), ("2", "♠")]
    monkeypatch.setattr(casino, "new_shoe", lambda decks: list(fixed_shoe))
    monkeypatch.setattr(casino.random, "shuffle", lambda seq: None)

    await dpytest.message("!casino blackjack 100", member=author)
    dpytest.get_message()

    casino_cog = bot.get_cog("Casino")
    hand_id, view = next(iter(casino_cog._bj_hands.items()))
    assert view.player_cards == [("2", "♠"), ("7", "♥")]

    await view._on_hit(_FakeInteraction(author.id))

    assert view.player_cards == [("2", "♠"), ("7", "♥"), ("5", "♣")]
    row = await db.get_blackjack_hand(hand_id)
    assert row is not None
    assert row["player"] == view.player_cards
    assert row["shoe"] == view.shoe
    # Bet is still just debited — the hand hasn't settled yet.
    assert await db.get_balance(author.id) == 400

    await casino_cog._end_blackjack(hand_id)
    await db.delete_blackjack_hand(hand_id)


@pytest.mark.cogs("cogs.casino")
async def test_blackjack_stand_settles_and_deletes_row(bot, monkeypatch):
    """deal → stand settles and removes the persisted row (bet resolved)."""
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)
    monkeypatch.setattr(casino, "generate_challenges", lambda *a, **k: [])

    # Dealt: player 2♠ 7♥ (9), dealer 3♦ 4♣ (7, must hit); next pop is 10♣,
    # bringing the dealer to 17 (stands). Player stands at 9 -> loses.
    fixed_shoe = [("10", "♣"), ("4", "♣"), ("3", "♦"), ("7", "♥"), ("2", "♠")]
    monkeypatch.setattr(casino, "new_shoe", lambda decks: list(fixed_shoe))
    monkeypatch.setattr(casino.random, "shuffle", lambda seq: None)

    await dpytest.message("!casino blackjack 100", member=author)
    dpytest.get_message()

    casino_cog = bot.get_cog("Casino")
    hand_id, view = next(iter(casino_cog._bj_hands.items()))

    interaction = _FakeInteraction(author.id)
    await view._on_stand(interaction)

    assert interaction.response.edited is not None
    assert await db.get_open_blackjack_hands() == []
    assert await db.get_blackjack_hand(hand_id) is None
    assert hand_id not in casino_cog._bj_hands
    assert hand_id not in casino_cog._bj_tasks
    # Player 9 loses to dealer 17: bet stays spent, no payout.
    assert await db.get_balance(author.id) == 400


@pytest.mark.cogs("cogs.casino")
async def test_blackjack_wrong_user_cannot_press_buttons(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    other = config().members[1]
    await db.add_coins(author.id, 500)

    fixed_shoe = [("5", "♣"), ("4", "♣"), ("3", "♦"), ("7", "♥"), ("2", "♠")]
    monkeypatch.setattr(casino, "new_shoe", lambda decks: list(fixed_shoe))
    monkeypatch.setattr(casino.random, "shuffle", lambda seq: None)

    await dpytest.message("!casino blackjack 100", member=author)
    dpytest.get_message()

    casino_cog = bot.get_cog("Casino")
    hand_id, view = next(iter(casino_cog._bj_hands.items()))

    allowed = await view.interaction_check(_FakeInteraction(other.id))
    assert allowed is False
    # The hand is untouched — still open, still the original 2-card hand.
    assert await db.get_blackjack_hand(hand_id) is not None
    assert len(view.player_cards) == 2

    await casino_cog._end_blackjack(hand_id)
    await db.delete_blackjack_hand(hand_id)


@pytest.mark.cogs("cogs.casino")
async def test_blackjack_double_settle_pays_out_once(bot, monkeypatch):
    """A button press racing the expiry timer (or a second concurrent settle
    attempt) must not pay out twice — the second call is a no-op."""
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)
    monkeypatch.setattr(casino, "generate_challenges", lambda *a, **k: [])

    fixed_shoe = [("10", "♣"), ("4", "♣"), ("3", "♦"), ("7", "♥"), ("2", "♠")]
    monkeypatch.setattr(casino, "new_shoe", lambda decks: list(fixed_shoe))
    monkeypatch.setattr(casino.random, "shuffle", lambda seq: None)

    await dpytest.message("!casino blackjack 100", member=author)
    dpytest.get_message()

    casino_cog = bot.get_cog("Casino")
    hand_id, view = next(iter(casino_cog._bj_hands.items()))

    await view._settle(_FakeInteraction(author.id))
    balance_after_first = await db.get_balance(author.id)
    second_interaction = _FakeInteraction(author.id)
    await view._settle(second_interaction)  # already _done -> early no-op

    assert await db.get_balance(author.id) == balance_after_first
    assert second_interaction.response.sent is not None  # got the "already settled" ack


@pytest.mark.cogs("cogs.casino")
async def test_blackjack_expiry_auto_stands_and_settles(bot, monkeypatch):
    """expiry auto-stand math: the dealer plays out and the hand settles as if
    the player had pressed Stand themselves."""
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)
    monkeypatch.setattr(casino, "generate_challenges", lambda *a, **k: [])

    # Same deterministic hand as the Stand test: player 9 vs dealer -> 17.
    fixed_shoe = [("10", "♣"), ("4", "♣"), ("3", "♦"), ("7", "♥"), ("2", "♠")]
    monkeypatch.setattr(casino, "new_shoe", lambda decks: list(fixed_shoe))
    monkeypatch.setattr(casino.random, "shuffle", lambda seq: None)

    await dpytest.message("!casino blackjack 100", member=author)
    dpytest.get_message()

    casino_cog = bot.get_cog("Casino")
    hand_id, view = next(iter(casino_cog._bj_hands.items()))
    assert hand_id in casino_cog._bj_tasks

    await view.expire()  # simulates the BJ_TIMEOUT firing with no button press

    assert await db.get_open_blackjack_hands() == []
    assert hand_id not in casino_cog._bj_hands
    assert hand_id not in casino_cog._bj_tasks
    assert await db.get_balance(author.id) == 400  # player 9 loses to 17

    # A second expire() call (e.g. a duplicate timer fire) is a safe no-op.
    await view.expire()
    assert await db.get_balance(author.id) == 400


@pytest.mark.cogs("cogs.casino")
async def test_blackjack_expiry_settles_even_without_a_live_view(bot):
    """Restart flow: a persisted hand with no in-memory view (simulating a
    fresh process) is rebuilt by on_restore_schedules, resumes with its exact
    saved cards/shoe, and its timer can still auto-settle it."""
    guild = config().guilds[0]
    author = config().members[0]
    channel = config().channels[0]
    await db.add_coins(author.id, 500)
    await db.try_debit_coins(author.id, 100)  # mirrors the real deal flow

    placeholder = await channel.send("placeholder for a restart-restored hand")
    # Player 9 vs dealer 7 (must hit); shoe's last card (10♣) brings the
    # dealer to 17 once the restored view auto-stands it.
    hand_id = await db.create_blackjack_hand(
        guild.id,
        channel.id,
        author.id,
        100,
        0,
        [("10", "♣")],
        [("2", "♠"), ("7", "♥")],
        [("3", "♦"), ("4", "♣")],
        time.time(),
    )
    await db.set_blackjack_message(hand_id, placeholder.id)

    casino_cog = bot.get_cog("Casino")
    casino_cog._bj_hands.clear()  # nothing live in memory yet, as after a restart

    await casino_cog.on_restore_schedules()

    assert hand_id in casino_cog._bj_hands
    assert hand_id in casino_cog._bj_tasks
    view = casino_cog._bj_hands[hand_id]
    assert view.bet == 100
    assert view.player_cards == [("2", "♠"), ("7", "♥")]
    assert view.dealer_cards == [("3", "♦"), ("4", "♣")]
    # dpytest's fake backend can't edit a bare PartialMessage (it needs a
    # full Message to build its response payload) — swap in the real one so
    # this test exercises the settle logic, not that test-double gap.
    view.message = placeholder

    await view.expire()

    assert await db.get_open_blackjack_hands() == []
    assert hand_id not in casino_cog._bj_hands
    assert await db.get_balance(author.id) == 400  # player 9 loses to 17


@pytest.mark.cogs("cogs.casino")
async def test_restore_schedules_settles_orphaned_hand_with_no_message(bot):
    """A crash between the row insert and the reply being sent leaves
    message_id NULL — restore must still resolve the bet rather than leave it
    debited forever, even with nothing to edit."""
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)
    await db.try_debit_coins(author.id, 100)  # mirrors the real deal flow

    hand_id = await db.create_blackjack_hand(
        guild.id,
        1,
        author.id,
        100,
        0,
        [("10", "♣")],
        [("2", "♠"), ("7", "♥")],
        [("3", "♦"), ("4", "♣")],
        time.time(),
    )
    # message_id was never set — simulating the crash window.

    casino_cog = bot.get_cog("Casino")
    casino_cog._bj_hands.clear()

    await casino_cog.on_restore_schedules()

    assert await db.get_open_blackjack_hands() == []
    assert hand_id not in casino_cog._bj_hands
    assert await db.get_balance(author.id) == 400  # player 9 loses to 17


# ── /casino challenge ────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.casino")
async def test_challenge_command_shows_two_challenges(bot):
    author = config().members[0]
    await dpytest.message("!casino challenge", member=author)
    sent = dpytest.get_message()
    assert "Challenges" in sent.embeds[0].title
    assert len(sent.embeds[0].fields) == 2


@pytest.mark.cogs("cogs.casino")
async def test_challenge_auto_claims_and_credits_reward(bot, monkeypatch):
    """A challenge whose target is already met by one game auto-claims and
    pays its reward the instant it completes — no double payment on a
    second game once claimed."""
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)

    one_challenge = [
        {
            "chal_key": "play_games",
            "target": 1,
            "reward": 150,
            "label": "Play 1 game",
        }
    ]
    monkeypatch.setattr(casino, "generate_challenges", lambda *a, **k: one_challenge)
    monkeypatch.setattr(casino.random, "random", lambda: 0.999999)  # flip -> loses

    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "🏆" in (sent.embeds[0].footer.text or "")
    # 500 - 100 (lost the flip) + 150 (challenge reward) = 550
    assert await db.get_balance(author.id) == 550

    today = int(time.time() // 86400)
    progress = await db.get_challenge_progress(author.id, today, "play_games")
    assert progress == {"progress": 1, "claimed": True}

    # A second game must not pay the reward again.
    await dpytest.message("!casino flip 100 heads", member=author)
    dpytest.get_message()
    assert await db.get_balance(author.id) == 450  # 550 - 100, no reward


@pytest.mark.cogs("cogs.casino")
async def test_challenge_progress_hint_shown_before_completion(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 500)

    one_challenge = [
        {
            "chal_key": "win_games",
            "target": 3,
            "reward": 150,
            "label": "Win 3 games",
        }
    ]
    monkeypatch.setattr(casino, "generate_challenges", lambda *a, **k: one_challenge)
    monkeypatch.setattr(casino.random, "random", lambda: 0.0)  # flip heads -> wins

    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    footer = sent.embeds[0].footer.text or ""
    assert "Challenge: 1/3 wins" in footer


@pytest.mark.cogs("cogs.casino")
async def test_stats_and_jackpot_and_overview_reply(bot):
    guild = config().guilds[0]
    author = config().members[0]

    await dpytest.message("!casino", member=author)
    sent = dpytest.get_message()
    assert "Casino" in sent.embeds[0].title

    await dpytest.message("!casino jackpot", member=author)
    sent = dpytest.get_message()
    assert "Jackpot" in sent.embeds[0].title

    await dpytest.message("!casino stats", member=author)
    sent = dpytest.get_message()
    assert author.display_name in sent.embeds[0].title


@pytest.mark.cogs("cogs.casino")
async def test_toggle_denied_without_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!casino toggle", member=author)


@pytest.mark.cogs("cogs.casino")
async def test_toggle_flips_config_and_blocks_play(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    await db.add_coins(author.id, 500)

    await dpytest.message("!casino toggle", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description
    assert (await db.get_casino_config(guild.id))["enabled"] is False

    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description
    assert await db.get_balance(author.id) == 500

    await dpytest.message("!casino toggle", member=author)
    sent = dpytest.get_message()
    assert "enabled" in sent.embeds[0].description
    assert (await db.get_casino_config(guild.id))["enabled"] is True


@pytest.mark.cogs("cogs.casino")
async def test_limit_denied_without_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!casino limit 5 50", member=author)


@pytest.mark.cogs("cogs.casino")
async def test_limit_updates_bounds_and_enforces_them(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    await db.add_coins(author.id, 500)

    await dpytest.message("!casino limit 5 50", member=author)
    sent = dpytest.get_message()
    assert "Bet limits set" in sent.embeds[0].description
    cfg = await db.get_casino_config(guild.id)
    assert cfg["min_bet"] == 5
    assert cfg["max_bet"] == 50

    # Now a bet of 100 is above the new max.
    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "between" in sent.embeds[0].description
    assert await db.get_balance(author.id) == 500


@pytest.mark.cogs("cogs.casino")
async def test_limit_rejects_max_below_min(bot):
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!casino limit 100 10", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title


@pytest.mark.cogs("cogs.casino")
async def test_config_requires_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!casino config", member=author)


@pytest.mark.cogs("cogs.casino")
async def test_config_shows_settings_with_perms(bot):
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!casino config", member=author)
    sent = dpytest.get_message()
    assert "Casino Settings" in sent.embeds[0].title
