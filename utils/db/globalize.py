"""utils/db.globalize — migration 1: per-guild economy → one global account.

Every economy table that stores a member's *progress* (wallet, inventory,
effects, fishing, casino, activities, achievements, prestige) used to be keyed
by ``(guild_id, user_id)``, so the same person had a separate, unrelated
economy in every server. They're now keyed by ``user_id`` alone. This module
holds the one-time rebuild that merges the old rows, and nothing else — no
tables of its own, no accessors.

Merge rules, per table, chosen so that **no member can lose value and nothing
can be paid out twice**:

  * Counters that represent things earned (coins, contribution, casts, catches,
    XP, games played, wagered/won, shift/dig counts) are **summed** — every one
    of those was legitimately earned, just in different rooms of the same house.
    Summing is the only rule that never destroys value; taking the max would
    silently delete a member's second-server progress. (It does mean someone
    with 1,000 coins in five servers now holds 5,000 in one wallet. That's the
    intended reading: their total holdings are unchanged, only the walls
    between them are gone.)
  * Permanent unlocks and "personal best" style values (rod tier, pickaxe tier,
    prestige rank, heaviest catch, biggest win, best streak) take the **max** —
    a member keeps the best thing they ever earned.
  * Cooldown stamps (``last_cast``, ``last_daily``, ``last_work``, …) take the
    **max**, i.e. the most recent claim anywhere. Taking the min would hand out
    a free extra claim in every server the member had ever used.
  * One-time claim flags (quest ``claimed``, challenge ``claimed``, weekly
    objective ``claimed``, achievement rows) are **OR-ed / kept**: if a reward
    was claimed in any server it stays claimed, so the merge can't re-open an
    already-paid reward.

Guild-owned data is untouched: every ``*_config`` table, the per-guild shop and
its purchase ledger, server economy events, live /raid + /squad boards, open
blackjack hands, and the leveling tables all keep their guild_id.

The migration is safe to re-run: each step checks whether the table still has a
guild_id column and skips it if the rebuild already happened (a fresh database
is created straight from the new schema, so every step is a no-op there).
"""

import logging

from ._core import migration

log = logging.getLogger("NanoBot.db")


async def _has_guild_column(conn, table: str) -> bool:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        cols = {row["name"] for row in await cur.fetchall()}
    return bool(cols) and "guild_id" in cols


async def _rebuild(conn, table: str, create_sql: str, insert_sql: str, indexes=()):
    """Merge one per-guild table into its user-scoped replacement.

    Builds the new table beside the old one, folds the rows in with
    `insert_sql`, then swaps the names. Runs inside the migration's
    transaction, so a failure anywhere rolls the whole thing back and the
    original table is still there on the next start.
    """
    if not await _has_guild_column(conn, table):
        return False
    log.info("Globalizing %s …", table)
    await conn.execute(f"DROP TABLE IF EXISTS {table}_globalized")
    await conn.execute(create_sql)
    await conn.execute(insert_sql)
    await conn.execute(f"DROP TABLE {table}")
    await conn.execute(f"ALTER TABLE {table}_globalized RENAME TO {table}")
    for index_sql in indexes:
        await conn.execute(index_sql)
    return True


@migration(1)
async def globalize_economy(conn):
    """Merge every per-(guild, user) economy row into one row per user."""
    # ── Wallet: coins and contribution sum; the daily claim keeps the most
    # recent stamp and the best streak reached.
    await _rebuild(
        conn,
        "economy",
        """
        CREATE TABLE economy_globalized (
            user_id      TEXT PRIMARY KEY,
            coins        INTEGER NOT NULL DEFAULT 0,
            last_daily   REAL NOT NULL DEFAULT 0,
            streak       INTEGER NOT NULL DEFAULT 0,
            contribution INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        INSERT INTO economy_globalized (user_id, coins, last_daily, streak, contribution)
        SELECT user_id, SUM(coins), MAX(last_daily), MAX(streak), SUM(contribution)
        FROM economy GROUP BY user_id
        """,
        (
            "CREATE INDEX IF NOT EXISTS economy_coins ON economy (coins DESC)",
            "CREATE INDEX IF NOT EXISTS economy_contrib ON economy (contribution DESC)",
        ),
    )

    # ── Inventory: stacks of the same item add up.
    await _rebuild(
        conn,
        "user_items",
        """
        CREATE TABLE user_items_globalized (
            user_id  TEXT NOT NULL,
            item_key TEXT NOT NULL,
            qty      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, item_key)
        )
        """,
        """
        INSERT INTO user_items_globalized (user_id, item_key, qty)
        SELECT user_id, item_key, SUM(qty) FROM user_items GROUP BY user_id, item_key
        """,
    )

    # ── Effects: keep the strongest magnitude, the latest expiry, and every
    # remaining charge the member paid for.
    await _rebuild(
        conn,
        "user_effects",
        """
        CREATE TABLE user_effects_globalized (
            user_id    TEXT NOT NULL,
            effect_key TEXT NOT NULL,
            magnitude  REAL NOT NULL DEFAULT 0,
            expires_at REAL NOT NULL DEFAULT 0,
            uses_left  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, effect_key)
        )
        """,
        """
        INSERT INTO user_effects_globalized
            (user_id, effect_key, magnitude, expires_at, uses_left)
        SELECT user_id, effect_key, MAX(magnitude), MAX(expires_at), SUM(uses_left)
        FROM user_effects GROUP BY user_id, effect_key
        """,
    )

    # ── Fishing. `best_key` comes from a correlated subquery rather than riding
    # along with MAX(best_weight): SQLite only guarantees bare columns match the
    # extreme row when the query has exactly ONE min/max aggregate, and this one
    # has several.
    await _rebuild(
        conn,
        "fishing_stats",
        """
        CREATE TABLE fishing_stats_globalized (
            user_id     TEXT PRIMARY KEY,
            rod_level   INTEGER NOT NULL DEFAULT 0,
            last_cast   REAL NOT NULL DEFAULT 0,
            casts       INTEGER NOT NULL DEFAULT 0,
            caught      INTEGER NOT NULL DEFAULT 0,
            earned      INTEGER NOT NULL DEFAULT 0,
            best_key    TEXT NOT NULL DEFAULT '',
            best_weight REAL NOT NULL DEFAULT 0,
            xp          INTEGER NOT NULL DEFAULT 0,
            streak_days INTEGER NOT NULL DEFAULT 0,
            last_day    INTEGER NOT NULL DEFAULT 0,
            -- Added after this migration (the fishing spot an angler is
            -- standing at), and listed here for the same reason as
            -- activities_stats' co-op columns: this rebuild REPLACES the
            -- table, so without it a database migrating for the first time
            -- comes out missing what _ensure_columns just added. Not in the
            -- INSERT below — the source table predates it, so everyone lands
            -- at the starter spot, which is what '' resolves to.
            spot        TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        INSERT INTO fishing_stats_globalized
            (user_id, rod_level, last_cast, casts, caught, earned, best_key,
             best_weight, xp, streak_days, last_day)
        SELECT f.user_id, MAX(f.rod_level), MAX(f.last_cast), SUM(f.casts),
               SUM(f.caught), SUM(f.earned), COALESCE((
                   SELECT f2.best_key FROM fishing_stats f2
                   WHERE f2.user_id = f.user_id
                   ORDER BY f2.best_weight DESC LIMIT 1
               ), ''), MAX(f.best_weight), SUM(f.xp), MAX(f.streak_days),
               MAX(f.last_day)
        FROM fishing_stats f GROUP BY f.user_id
        """,
        (
            "CREATE INDEX IF NOT EXISTS fishing_stats_earned "
            "ON fishing_stats (earned DESC)",
        ),
    )

    # ── The bag: individual catches, so this is a straight copy minus guild_id.
    await _rebuild(
        conn,
        "fishing_catches",
        """
        CREATE TABLE fishing_catches_globalized (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL,
            fish_key  TEXT NOT NULL,
            weight    REAL NOT NULL DEFAULT 0,
            value     INTEGER NOT NULL DEFAULT 0,
            caught_at REAL NOT NULL DEFAULT 0
        )
        """,
        """
        INSERT INTO fishing_catches_globalized
            (id, user_id, fish_key, weight, value, caught_at)
        SELECT id, user_id, fish_key, weight, value, caught_at FROM fishing_catches
        """,
        (
            "CREATE INDEX IF NOT EXISTS fishing_catches_user "
            "ON fishing_catches (user_id, fish_key)",
        ),
    )

    await _rebuild(
        conn,
        "fishing_species",
        """
        CREATE TABLE fishing_species_globalized (
            user_id  TEXT NOT NULL,
            fish_key TEXT NOT NULL,
            caught   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, fish_key)
        )
        """,
        """
        INSERT INTO fishing_species_globalized (user_id, fish_key, caught)
        SELECT user_id, fish_key, SUM(caught)
        FROM fishing_species GROUP BY user_id, fish_key
        """,
    )

    # ── Daily quest: keep the furthest-along row for the day, and a claimed
    # one always wins (claimed*1e9 dominates the ranking key), so a reward
    # already paid in one server can't be claimed again in another.
    await _rebuild(
        conn,
        "fishing_quests",
        """
        CREATE TABLE fishing_quests_globalized (
            user_id   TEXT NOT NULL,
            day       INTEGER NOT NULL,
            quest_key TEXT NOT NULL,
            target    INTEGER NOT NULL DEFAULT 0,
            progress  INTEGER NOT NULL DEFAULT 0,
            claimed   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )
        """,
        """
        INSERT INTO fishing_quests_globalized
            (user_id, day, quest_key, target, progress, claimed)
        SELECT user_id, day, quest_key, target, progress, claimed
        FROM (
            SELECT user_id, day, quest_key, target, progress, claimed,
                   MAX(claimed * 1000000000 + progress)
            FROM fishing_quests GROUP BY user_id, day
        )
        """,
    )

    # ── Casino: lifetime totals add up, records and streaks keep the best.
    await _rebuild(
        conn,
        "casino_stats",
        """
        CREATE TABLE casino_stats_globalized (
            user_id     TEXT PRIMARY KEY,
            games       INTEGER NOT NULL DEFAULT 0,
            wagered     INTEGER NOT NULL DEFAULT 0,
            won         INTEGER NOT NULL DEFAULT 0,
            biggest_win INTEGER NOT NULL DEFAULT 0,
            streak      INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0,
            wins        INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        INSERT INTO casino_stats_globalized
            (user_id, games, wagered, won, biggest_win, streak, best_streak, wins)
        SELECT user_id, SUM(games), SUM(wagered), SUM(won), MAX(biggest_win),
               MAX(streak), MAX(best_streak), SUM(wins)
        FROM casino_stats GROUP BY user_id
        """,
        (
            "CREATE INDEX IF NOT EXISTS casino_stats_net "
            "ON casino_stats ((won - wagered) DESC)",
        ),
    )

    await _rebuild(
        conn,
        "casino_challenges",
        """
        CREATE TABLE casino_challenges_globalized (
            user_id  TEXT NOT NULL,
            day      INTEGER NOT NULL,
            chal_key TEXT NOT NULL,
            target   INTEGER NOT NULL DEFAULT 0,
            progress INTEGER NOT NULL DEFAULT 0,
            claimed  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day, chal_key)
        )
        """,
        """
        INSERT INTO casino_challenges_globalized
            (user_id, day, chal_key, target, progress, claimed)
        SELECT user_id, day, chal_key, target, progress, claimed
        FROM (
            SELECT user_id, day, chal_key, target, progress, claimed,
                   MAX(claimed * 1000000000 + progress)
            FROM casino_challenges GROUP BY user_id, day, chal_key
        )
        """,
    )

    # ── Activities: counts add up, the pickaxe keeps its best tier, and every
    # cooldown keeps the most recent claim (never a free extra run).
    await _rebuild(
        conn,
        "activities_stats",
        """
        CREATE TABLE activities_stats_globalized (
            user_id         TEXT PRIMARY KEY,
            last_work       REAL NOT NULL DEFAULT 0,
            work_shifts     INTEGER NOT NULL DEFAULT 0,
            last_mine       REAL NOT NULL DEFAULT 0,
            mine_count      INTEGER NOT NULL DEFAULT 0,
            pickaxe_level   INTEGER NOT NULL DEFAULT 0,
            last_hunt       REAL NOT NULL DEFAULT 0,
            hunt_count      INTEGER NOT NULL DEFAULT 0,
            last_explore    REAL NOT NULL DEFAULT 0,
            explore_count   INTEGER NOT NULL DEFAULT 0,
            last_rob        REAL NOT NULL DEFAULT 0,
            rob_count       INTEGER NOT NULL DEFAULT 0,
            -- Added later than this migration (the /squad + /raid claim, then
            -- the adventure daily streak), and listed here rather than only in
            -- _ensure_columns because this rebuild REPLACES the table: without
            -- them a database migrating for the first time would come out of
            -- migration 1 missing the columns _ensure_columns had already
            -- added. They are not in the INSERT below — the source table
            -- predates them, so every row starts at the defaults, i.e. nobody's
            -- first co-op is blocked and everyone's streak starts at day one.
            last_coop       REAL NOT NULL DEFAULT 0,
            coop_count      INTEGER NOT NULL DEFAULT 0,
            last_raid       REAL NOT NULL DEFAULT 0,
            raid_count      INTEGER NOT NULL DEFAULT 0,
            streak_days     INTEGER NOT NULL DEFAULT 0,
            last_day        INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        INSERT INTO activities_stats_globalized
            (user_id, last_work, work_shifts, last_mine, mine_count, pickaxe_level,
             last_hunt, hunt_count, last_explore, explore_count, last_rob, rob_count)
        SELECT user_id, MAX(last_work), SUM(work_shifts), MAX(last_mine),
               SUM(mine_count), MAX(pickaxe_level), MAX(last_hunt), SUM(hunt_count),
               MAX(last_explore), SUM(explore_count), MAX(last_rob), SUM(rob_count)
        FROM activities_stats GROUP BY user_id
        """,
    )

    # ── Progression. An achievement earned in any server keeps its *earliest*
    # award time, so the badge history reads true and it can never be re-awarded
    # (the cog only pays out when the INSERT actually lands).
    await _rebuild(
        conn,
        "achievements_earned",
        """
        CREATE TABLE achievements_earned_globalized (
            user_id   TEXT NOT NULL,
            key       TEXT NOT NULL,
            earned_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, key)
        )
        """,
        """
        INSERT INTO achievements_earned_globalized (user_id, key, earned_at)
        SELECT user_id, key, MIN(earned_at)
        FROM achievements_earned GROUP BY user_id, key
        """,
    )

    # ── Weekly objectives: the lowest baseline (the member's best progress this
    # period) and claimed-anywhere-stays-claimed. Merged lifetime stats can push
    # an in-flight objective straight to complete — a one-off for the current
    # period only, and it can't double-pay because claimed carried over.
    await _rebuild(
        conn,
        "weekly_objectives",
        """
        CREATE TABLE weekly_objectives_globalized (
            user_id  TEXT NOT NULL,
            week     TEXT NOT NULL,
            obj_key  TEXT NOT NULL,
            target   REAL NOT NULL DEFAULT 0,
            baseline REAL NOT NULL DEFAULT 0,
            claimed  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, week, obj_key)
        )
        """,
        """
        INSERT INTO weekly_objectives_globalized
            (user_id, week, obj_key, target, baseline, claimed)
        SELECT user_id, week, obj_key, MIN(target), MIN(baseline), MAX(claimed)
        FROM weekly_objectives GROUP BY user_id, week, obj_key
        """,
    )

    # ── Prestige + displayed title: highest rank wins, and the title comes from
    # whichever server that rank was reached in (falling back to any title the
    # member had picked).
    await _rebuild(
        conn,
        "progression",
        """
        CREATE TABLE progression_globalized (
            user_id        TEXT PRIMARY KEY,
            prestige       INTEGER NOT NULL DEFAULT 0,
            selected_title TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        INSERT INTO progression_globalized (user_id, prestige, selected_title)
        SELECT p.user_id, MAX(p.prestige), COALESCE((
            SELECT p2.selected_title FROM progression p2
            WHERE p2.user_id = p.user_id AND p2.selected_title <> ''
            ORDER BY p2.prestige DESC LIMIT 1
        ), '')
        FROM progression p GROUP BY p.user_id
        """,
    )


@migration(2)
async def drop_fishing_cooldown_setting(conn):
    """Retire the per-guild cast cooldown — it's one fixed value now.

    The cast claim is per user, so a per-server cooldown stopped meaning
    anything the moment a member fished in two servers (whichever server they
    cast in last set everyone's pace). `cogs.fishing.constants.CAST_COOLDOWN`
    replaces the column; the guild keeps its on/off switch. Idempotent: skipped
    once the column is gone.
    """
    async with conn.execute("PRAGMA table_info(fishing_config)") as cur:
        cols = {row["name"] for row in await cur.fetchall()}
    if "cooldown" not in cols:
        return
    log.info("Dropping the per-guild fishing cooldown setting …")
    await conn.execute("DROP TABLE IF EXISTS fishing_config_new")
    await conn.execute(
        "CREATE TABLE fishing_config_new ("
        "guild_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1)"
    )
    await conn.execute(
        "INSERT INTO fishing_config_new (guild_id, enabled) "
        "SELECT guild_id, enabled FROM fishing_config"
    )
    await conn.execute("DROP TABLE fishing_config")
    await conn.execute("ALTER TABLE fishing_config_new RENAME TO fishing_config")


@migration(3)
async def rescale_default_casino_limits(conn):
    """Move guilds still on the old bet caps onto the rebalanced defaults.

    The cast cooldown drop (60s -> 20s) tripled hourly income, so a 1,000-coin
    ceiling stopped being a meaningful stake. Only rows that still hold the
    *exact* old defaults are touched — a guild that ran `/casino limit` picked
    its own numbers and keeps them. Safe to re-run: after the update no row
    matches the old pair any more (unless an admin deliberately sets it back,
    which is their call and this migration won't run again anyway).
    """
    cur = await conn.execute(
        "UPDATE casino_config SET min_bet=25, max_bet=3000 "
        "WHERE min_bet=10 AND max_bet=1000"
    )
    if cur.rowcount:
        log.info("Rescaled default casino bet limits for %d guild(s)", cur.rowcount)


@migration(4)
async def drop_per_guild_activity_cooldowns(conn):
    """Retire the per-guild activity cooldown lengths — they're bot-wide now.

    Same reasoning as migration 2 did for fishing: the claim is keyed by user
    id alone, so whichever of a member's servers set the shortest length was
    quietly setting the pace for all of them, and the coins it minted spend
    everywhere. The lengths moved to `bot_settings` (utils/db/settings.py),
    where only the bot owner can write them; the guild keeps its on/off
    switches.

    Per-guild values can't be folded into one bot-wide number, so they are
    dropped and every activity returns to its default — the count is logged so
    the owner knows to re-set anything they cared about with `!cooldown`.
    Idempotent: skipped once the columns are gone.
    """
    async with conn.execute("PRAGMA table_info(activities_config)") as cur:
        cols = {row["name"] for row in await cur.fetchall()}
    if "work_cooldown" not in cols:
        return
    async with conn.execute(
        "SELECT COUNT(*) FROM activities_config WHERE "
        "work_cooldown<>3600 OR mine_cooldown<>1800 OR hunt_cooldown<>2700 OR "
        "explore_cooldown<>10800 OR rob_cooldown<>14400"
    ) as cur:
        customised = (await cur.fetchone())[0]
    log.info("Dropping the per-guild activity cooldown settings …")
    await conn.execute("DROP TABLE IF EXISTS activities_config_new")
    await conn.execute(
        "CREATE TABLE activities_config_new ("
        "guild_id TEXT PRIMARY KEY, "
        "work_enabled INTEGER NOT NULL DEFAULT 1, "
        "mine_enabled INTEGER NOT NULL DEFAULT 1, "
        "hunt_enabled INTEGER NOT NULL DEFAULT 1, "
        "explore_enabled INTEGER NOT NULL DEFAULT 1, "
        "rob_enabled INTEGER NOT NULL DEFAULT 1)"
    )
    await conn.execute(
        "INSERT INTO activities_config_new (guild_id, work_enabled, mine_enabled, "
        "hunt_enabled, explore_enabled, rob_enabled) "
        "SELECT guild_id, work_enabled, mine_enabled, hunt_enabled, "
        "explore_enabled, rob_enabled FROM activities_config"
    )
    await conn.execute("DROP TABLE activities_config")
    await conn.execute("ALTER TABLE activities_config_new RENAME TO activities_config")
    if customised:
        log.warning(
            "%d guild(s) had custom activity cooldowns; every activity is back "
            "on its default — set bot-wide lengths with !cooldown",
            customised,
        )


@migration(5)
async def drop_per_guild_coin_faucets(conn):
    """Retire the per-guild coin *faucet* amounts — they're bot-wide now.

    The last per-guild knobs that minted into a global wallet: the /daily
    amount and its streak bonus, the /squad and /raid per-person rewards, and
    the level-up coin payout. Each claim they pay out is global (one /daily per
    account, one wallet everywhere), so a server raising any of them was paying
    its members coins that spend in every other server. They moved to
    `bot_settings` (utils/db/settings.py), behind the owner-only `!econ`.

    Everything a guild keeps here is either cosmetic or a pure *sink*: its
    currency name and emoji, its raid party size, and — untouched by this and
    deliberately so — its shop prices, which destroy coins in exchange for that
    guild's own roles and perks and so can't affect anyone outside it.

    Per-guild amounts can't be folded into one bot-wide number, so they are
    dropped back to the defaults and the count is logged. Idempotent: skipped
    once the columns are gone.
    """
    async with conn.execute("PRAGMA table_info(economy_config)") as cur:
        econ_cols = {row["name"] for row in await cur.fetchall()}
    if "daily_amount" in econ_cols:
        async with conn.execute(
            "SELECT COUNT(*) FROM economy_config WHERE daily_amount<>100 OR "
            "streak_bonus<>0 OR coop_reward<>50 OR raid_reward<>100"
        ) as cur:
            customised = (await cur.fetchone())[0]
        log.info("Dropping the per-guild coin faucet amounts …")
        await conn.execute("DROP TABLE IF EXISTS economy_config_new")
        await conn.execute(
            "CREATE TABLE economy_config_new ("
            "guild_id TEXT PRIMARY KEY, "
            "currency_name TEXT NOT NULL DEFAULT 'NanoCoin', "
            "currency_emoji TEXT NOT NULL DEFAULT '🪙', "
            "raid_min INTEGER NOT NULL DEFAULT 3, "
            "raid_max INTEGER NOT NULL DEFAULT 20)"
        )
        await conn.execute(
            "INSERT INTO economy_config_new "
            "(guild_id, currency_name, currency_emoji, raid_min, raid_max) "
            "SELECT guild_id, currency_name, currency_emoji, raid_min, raid_max "
            "FROM economy_config"
        )
        await conn.execute("DROP TABLE economy_config")
        await conn.execute("ALTER TABLE economy_config_new RENAME TO economy_config")
        if customised:
            log.warning(
                "%d guild(s) had custom daily/co-op/raid rewards; all are back "
                "on their defaults — set bot-wide amounts with !econ",
                customised,
            )

    async with conn.execute("PRAGMA table_info(level_config)") as cur:
        level_cols = {row["name"] for row in await cur.fetchall()}
    if "coin_reward" not in level_cols:
        return
    log.info("Dropping the per-guild level-up coin reward …")
    await conn.execute("DROP TABLE IF EXISTS level_config_new")
    await conn.execute(
        "CREATE TABLE level_config_new ("
        "guild_id TEXT PRIMARY KEY, "
        "enabled INTEGER NOT NULL DEFAULT 0, "
        "xp_min INTEGER NOT NULL DEFAULT 15, "
        "xp_max INTEGER NOT NULL DEFAULT 25, "
        "cooldown INTEGER NOT NULL DEFAULT 60, "
        "announce_channel TEXT, "
        "announce INTEGER NOT NULL DEFAULT 1, "
        # Added after this migration (the global level-up channel), and listed
        # here for the same reason as activities_stats' co-op columns: this
        # rebuild REPLACES the table, so without them a database migrating for
        # the first time comes out missing what _ensure_columns just added.
        # Not in the INSERT below — the source table predates them, so every
        # guild starts on the default (announce, following /level announce).
        "global_announce INTEGER NOT NULL DEFAULT 1, "
        "global_announce_channel TEXT)"
    )
    await conn.execute(
        "INSERT INTO level_config_new (guild_id, enabled, xp_min, xp_max, cooldown, "
        "announce_channel, announce) "
        "SELECT guild_id, enabled, xp_min, xp_max, cooldown, announce_channel, "
        "announce FROM level_config"
    )
    await conn.execute("DROP TABLE level_config")
    await conn.execute("ALTER TABLE level_config_new RENAME TO level_config")
