/**
 * views/settings.js
 * Every configurable feature, one screen each.
 *
 * The shape of every screen is the same: a sentence about what the feature does,
 * then settings that save themselves as you change them, then — where it helps —
 * a live preview. There is no Save button on a toggle, because a toggle that
 * needs saving is a toggle people forget to save; the ones that *do* have a
 * commit step (adding a shop item, a word to a list) are the ones where a
 * half-finished form shouldn't be written at all.
 *
 * Two screens are more than forms:
 *
 *   **AutoMod** is a rule builder. Every rule states in plain language what it
 *   catches and shows an example of a message that would trip it, and the fields
 *   are named for what they mean rather than for the column they live in. Regex
 *   is behind an "advanced" disclosure, checked server-side for catastrophic
 *   backtracking before it can be saved.
 *
 *   **Welcome & leave** is a message designer with a Discord-shaped preview that
 *   updates as you type, and variable tokens you tap to insert. Watching the
 *   preview is the whole point — the alternative is saving, joining with an alt,
 *   and looking.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as store from "../core/store.js";
import * as ui from "../core/ui.js";
import * as router from "../core/router.js";

const PAGES = [
  { key: "automod", label: "AutoMod", glyph: "🛡️", blurb: "Catch spam, invites, links and words automatically." },
  { key: "welcome", label: "Welcome", glyph: "👋", blurb: "Greet new members, with a live preview." },
  { key: "leave", label: "Leave messages", glyph: "🚪", blurb: "Say something when someone goes." },
  { key: "logging", label: "Logging", glyph: "📋", blurb: "Which server events get written to a channel." },
  { key: "leveling", label: "Leveling", glyph: "📈", blurb: "Message XP, level-up announcements and role rewards." },
  { key: "economy", label: "Economy", glyph: "🪙", blurb: "Your currency, your shop, and the rewards queue." },
  { key: "games", label: "Games", glyph: "🎮", blurb: "Switch fishing, activities and the casino on or off." },
  { key: "tickets", label: "Tickets", glyph: "🎫", blurb: "Private-thread support tickets and the panel members open them from." },
  { key: "birthdays", label: "Birthdays", glyph: "🎂", blurb: "Announce members' birthdays in their server's own timezone." },
  { key: "gatekeeper", label: "Gatekeeper", glyph: "🚧", blurb: "Hold new or suspicious accounts until they verify." },
  { key: "music", label: "Music", glyph: "🎵", blurb: "The server playlist, 24/7 mode, and the block lists." },
];

export async function settingsIndex({ guildId }) {
  return h(
    "div",
    { class: "stack" },
    h("h1", {}, "Settings"),
    h(
      "p",
      { class: "muted" },
      "Everything here is the same setting the Discord commands change. " +
        "Changes take effect immediately."
    ),
    h(
      "div",
      { class: "grid grid--wide" },
      ...[
        ...PAGES,
        // Self roles has a page of its own rather than a settings form: it is a
        // drag-and-drop editor, not a list of fields.
        {
          key: "roles",
          label: "Self roles",
          glyph: "🎭",
          blurb: "Panels of buttons members tap to give themselves a role.",
          path: `/g/${guildId}/roles`,
        },
      ].map((page) =>
        h(
          "a",
          {
            class: "card row",
            href: page.path || `/g/${guildId}/settings/${page.key}`,
            style: { textDecoration: "none", color: "inherit" },
          },
          h("span", { class: "icon-tile" }, page.glyph),
          h(
            "div",
            { class: "grow" },
            h("strong", {}, page.label),
            h("div", { class: "tiny dim" }, page.blurb)
          ),
          h("span", { class: "dim" }, "›")
        )
      )
    )
  );
}

export async function settingsPage({ guildId, feature }) {
  const views = {
    automod: automodPage,
    welcome: (id) => greetingPage(id, "welcome"),
    leave: (id) => greetingPage(id, "leave"),
    logging: loggingPage,
    leveling: levelingPage,
    economy: economyPage,
    games: gamesPage,
    tickets: ticketsPage,
    birthdays: birthdaysPage,
    gatekeeper: gatekeeperPage,
    music: musicPage,
  };
  const view = views[feature];
  if (!view) return ui.empty("No such setting", "Pick one from the settings list.", { glyph: "🤷" });
  const body = await view(guildId);
  const meta = PAGES.find((page) => page.key === feature);
  return h(
    "div",
    { class: "stack" },
    h(
      "div",
      { class: "row" },
      h("a", { class: "btn btn--ghost btn--sm", href: `/g/${guildId}/settings` }, "‹ Settings"),
      h("h1", { class: "grow" }, meta?.label || feature)
    ),
    meta ? h("p", { class: "muted" }, meta.blurb) : null,
    body
  );
}

/* ══════════════════════════════════════════════════════════════════════════
   AutoMod
   ══════════════════════════════════════════════════════════════════════════ */
async function automodPage(guildId) {
  const path = `/api/guilds/${guildId}/settings/automod`;
  let data = await api.get(path);
  const host = h("div", { class: "stack" });

  const save = (patch) => api.patch(path, patch);

  async function reload() {
    data = await api.get(path, { fresh: true });
    paint();
  }

  function paint() {
    const config = data.config;
    const active = Object.entries(config.rules || {}).filter(([, rule]) => rule.enabled).length;

    fill(
      host,
      ui.card(
        {},
        ui.cardHead("AutoMod", {
          glyph: "🛡️",
          sub: config.enabled ? `${fmt.plural(active, "rule")} active` : "Switched off",
        }),
        ui.switchRow(
          "Enable AutoMod",
          "Nothing below does anything while this is off.",
          config.enabled,
          async (value) => {
            await save({ enabled: value });
            config.enabled = value;
            paint();
          }
        ),
        config.enabled && !active
          ? ui.banner("AutoMod is on but no rules are switched on, so nothing will happen.", {
              kind: "warn",
              glyph: "⚠️",
            })
          : null
      ),

      ...Object.entries(data.meta.rules)
        .filter(([, spec]) => !spec.advanced)
        .map(([key, spec]) => ruleCard(key, spec)),

      h(
        "details",
        { class: "card" },
        h("summary", { style: { cursor: "pointer", fontWeight: 600 } }, "Advanced"),
        h(
          "div",
          { class: "stack", style: { marginTop: "var(--s-3)" } },
          ...Object.entries(data.meta.rules)
            .filter(([, spec]) => spec.advanced)
            .map(([key, spec]) => ruleCard(key, spec))
        )
      ),

      ui.card(
        {},
        ui.cardHead("Where actions get logged", { glyph: "📋" }),
        channelPicker(guildId, config.log_channel_id, async (value) => {
          await save({ log_channel_id: value });
        }, "Falls back to your main log channel when unset.")
      )
    );
  }

  function ruleCard(key, spec) {
    const rule = data.config.rules?.[key] || {};
    const enabled = Boolean(rule.enabled);
    const body = h("div", { class: "stack", style: { marginTop: "var(--s-3)" } });

    const paintBody = () => {
      if (!enabled) {
        // The switch row already carries the summary as its `why` while the
        // rule is off — repeating it here printed the same sentence twice.
        fill(body);
        return;
      }
      fill(
        body,
        h("p", { class: "small muted" }, spec.summary),
        spec.example
          ? h(
              "div",
              { class: "tile tiny" },
              h("span", { class: "dim" }, "Would catch: "),
              h("code", {}, spec.example)
            )
          : null,
        h(
          "div",
          { class: "grid grid--2" },
          ...spec.fields.map((field) => {
            const input = h("input", {
              class: "input",
              type: "number",
              min: String(field.min),
              max: String(field.max),
              value: String(rule[field.key] ?? field.default),
            });
            ui.autoSave(input, async (value) => {
              await save({ rules: { [key]: { [field.key]: Number(value) } } });
              rule[field.key] = Number(value);
            });
            return ui.field(field.label, input, {
              hint: `${field.min}–${field.max}`,
            });
          })
        ),
        actionPicker(key, rule),
        spec.list ? listEditor(spec.list) : null
      );
    };

    const card = ui.card(
      { class: enabled ? "card--accent" : "" },
      ui.switchRow(
        spec.label,
        enabled ? null : spec.summary,
        enabled,
        async (value) => {
          await save({ rules: { [key]: { enabled: value } } });
          data.config.rules = data.config.rules || {};
          data.config.rules[key] = { ...rule, enabled: value };
          paint();
        }
      ),
      body
    );
    paintBody();
    return card;
  }

  function actionPicker(key, rule) {
    const select = h(
      "select",
      { class: "select" },
      ...data.meta.actions.map((action) =>
        h("option", { value: action.key, selected: (rule.action || "delete") === action.key }, action.label)
      )
    );
    ui.autoSave(select, async (value) => {
      await save({ rules: { [key]: { action: value } } });
      rule.action = value;
    });
    return ui.field("What happens", select, {
      hint: "Every action deletes the message first.",
    });
  }

  /* The three word/pattern lists share one editor — they differ only in what a
     bad entry means, and the server is what decides that. */
  function listEditor(which) {
    const values = data.lists[which] || [];
    const input = h("input", {
      class: "input",
      placeholder: which === "regex" ? "A regular expression" : "A word or phrase",
      "aria-label": "New entry",
    });

    const add = async () => {
      const value = input.value.trim();
      if (!value) return;
      const result = await api.patch(`${api_listPath(guildId, which)}`, { value, action: "add" });
      data.lists[which] = result.values;
      input.value = "";
      paint();
    };

    return h(
      "div",
      { class: "stack stack--tight" },
      h("div", { class: "small muted" }, `${fmt.plural(values.length, "entry", "entries")}`),
      h(
        "div",
        { class: "row" },
        h("span", { class: "grow" }, input),
        ui.actionButton("Add", add, { size: "sm", variant: "primary" })
      ),
      values.length
        ? h(
            "div",
            { class: "row row--wrap" },
            // badwords and attachment_words come back as plain strings; the
            // regex list comes back as {id, pattern, label}. Rendering the row
            // itself printed [object Object] and then sent the whole object as
            // the value to delete, so nothing was ever removed.
            ...values.map((value) => {
              const pattern = typeof value === "string" ? value : value.pattern;
              const label = typeof value === "string" ? null : value.label;
              return h(
                "button",
                {
                  class: "chip",
                  type: "button",
                  title: label ? `${label} — remove` : "Remove",
                  onClick: async () => {
                    const result = await api.patch(api_listPath(guildId, which), {
                      value: pattern,
                      action: "remove",
                    });
                    data.lists[which] = result.values;
                    paint();
                  },
                },
                which === "regex" ? h("code", { class: "tiny" }, pattern) : pattern,
                label ? h("span", { class: "tiny dim" }, ` · ${label}`) : null,
                h("span", { class: "dim" }, "✕")
              );
            })
          )
        : h("p", { class: "tiny dim" }, "Nothing here yet — the rule won't catch anything until you add one."),
      which === "regex"
        ? ui.banner(
            "Patterns are checked before they're saved: one that can hang the " +
              "matcher on the wrong input is refused rather than let loose on live chat.",
            { kind: "warn", glyph: "⚠️" }
          )
        : null
    );
  }

  paint();
  return host;
}

const api_listPath = (guildId, which) =>
  `/api/guilds/${guildId}/settings/automod/lists/${which}`;

/* ══════════════════════════════════════════════════════════════════════════
   Welcome / leave — the message designer
   ══════════════════════════════════════════════════════════════════════════ */
async function greetingPage(guildId, kind) {
  const path = `/api/guilds/${guildId}/settings/greeting/${kind}`;
  const data = await api.get(path);
  const config = { ...data.config };
  const host = h("div", { class: "stack" });
  const preview = h("div", { class: "preview" });

  const save = async (patch) => {
    Object.assign(config, patch);
    await api.patch(path, patch);
  };

  /* The preview substitutes the same variables the bot does, with sample
     values, so what you read is what a member will read. */
  function renderPreview() {
    const user = store.state.user;
    const sample = (text) =>
      String(text || "")
        .replaceAll("{user}", `@${user?.name || "newcomer"}`)
        .replaceAll("{username}", user?.name || "newcomer")
        .replaceAll("{server}", store.state.guild?.name || "this server")
        .replaceAll("{membercount}", "1,204");

    fill(
      preview,
      config.dm
        ? h("div", { class: "tiny dim", style: { marginBottom: "var(--s-2)" } }, "Sent as a direct message")
        : null,
      h(
        "div",
        {
          class: "preview__embed",
          style: { borderLeftColor: config.color || "var(--brand)" },
        },
        h(
          "div",
          { class: "grow" },
          config.title ? h("div", { class: "preview__title" }, sample(config.title)) : null,
          h(
            "div",
            { class: "preview__content" },
            sample(config.content) ||
              h("span", { style: { opacity: 0.5 } }, "Your message will appear here.")
          ),
          config.image_url
            ? h("img", { class: "preview__img", src: config.image_url, alt: "", loading: "lazy" })
            : null,
          config.footer_text ? h("div", { class: "preview__footer" }, sample(config.footer_text)) : null
        ),
        config.thumbnail && user?.avatar
          ? h("img", { class: "preview__thumb", src: user.avatar, alt: "" })
          : null
      )
    );
  }

  function paint() {
    const contentInput = h("textarea", {
      class: "textarea",
      value: config.content || "",
      placeholder:
        kind === "welcome"
          ? "Welcome to {server}, {user}! You're member {membercount}."
          : "{username} has left {server}.",
    });
    contentInput.addEventListener("input", () => {
      config.content = contentInput.value;
      renderPreview();
    });
    ui.autoSave(contentInput, (value) => save({ content: value }));

    const titleInput = h("input", { class: "input", value: config.title || "", placeholder: "A heading (optional)" });
    titleInput.addEventListener("input", () => {
      config.title = titleInput.value;
      renderPreview();
    });
    ui.autoSave(titleInput, (value) => save({ title: value }));

    const footerInput = h("input", { class: "input", value: config.footer_text || "", placeholder: "Small print (optional)" });
    footerInput.addEventListener("input", () => {
      config.footer_text = footerInput.value;
      renderPreview();
    });
    ui.autoSave(footerInput, (value) => save({ footer_text: value }));

    const colourInput = h("input", { class: "input", type: "color", value: config.color || "#5865f2", style: { padding: "4px", height: "44px" } });
    colourInput.addEventListener("input", () => {
      config.color = colourInput.value;
      renderPreview();
    });
    ui.autoSave(colourInput, (value) => save({ color: value }));

    const imageInput = h("input", { class: "input", value: config.image_url || "", placeholder: "https://…" });
    imageInput.addEventListener("input", () => {
      config.image_url = imageInput.value;
      renderPreview();
    });
    ui.autoSave(imageInput, (value) => save({ image_url: value }));

    fill(
      host,
      ui.card(
        {},
        ui.switchRow(
          kind === "welcome" ? "Greet new members" : "Say goodbye",
          config.channel_id || config.dm
            ? null
            : "Pick a channel first — a message with nowhere to go is refused.",
          config.enabled,
          async (value, input) => {
            try {
              await save({ enabled: value });
            } catch (error) {
              input.checked = !value;
              throw error;
            }
          }
        )
      ),

      h(
        "div",
        { class: "split" },
        h(
          "div",
          { class: "stack" },
          ui.card(
            {},
            ui.cardHead("Where it goes", { glyph: "📍" }),
            channelPicker(guildId, config.channel_id, (value) => save({ channel_id: value })),
            ui.switchRow(
              "Send as a direct message instead",
              "Useful for rules or a private welcome. Members with DMs closed won't get it.",
              config.dm,
              (value) => {
                config.dm = value;
                renderPreview();
                return save({ dm: value });
              }
            )
          ),
          ui.card(
            {},
            ui.cardHead("The message", { glyph: "✍️" }),
            ui.field("Message", contentInput, { hint: "Tap a variable below to insert it." }),
            h(
              "div",
              { class: "row row--wrap" },
              ...data.meta.variables.map((variable) =>
                h(
                  "button",
                  {
                    class: "var-token",
                    type: "button",
                    title: `${variable.means} — e.g. ${variable.sample}`,
                    onClick: () => {
                      const start = contentInput.selectionStart ?? contentInput.value.length;
                      contentInput.value =
                        contentInput.value.slice(0, start) +
                        variable.token +
                        contentInput.value.slice(contentInput.selectionEnd ?? start);
                      contentInput.dispatchEvent(new Event("input"));
                      contentInput.focus();
                    },
                  },
                  variable.token
                )
              )
            ),
            ui.field("Title", titleInput),
            ui.field("Footer", footerInput),
            ui.field("Colour", colourInput),
            ui.field("Image URL", imageInput, { hint: "Shown under the message." }),
            ui.switchRow(
              "Show their avatar",
              "As a thumbnail on the right.",
              config.thumbnail,
              (value) => {
                config.thumbnail = value;
                renderPreview();
                return save({ thumbnail: value });
              }
            )
          )
        ),
        h(
          "div",
          { class: "stack" },
          ui.card(
            {},
            ui.cardHead("Preview", { glyph: "👁️", sub: "Live, with your own account as the sample" }),
            preview
          ),
          ui.banner(
            "Variables are filled in when the message is actually sent, so the " +
              "member count above is a stand-in.",
            { kind: "info", glyph: "💡" }
          )
        )
      )
    );
    renderPreview();
  }

  paint();
  return host;
}

/* ══════════════════════════════════════════════════════════════════════════
   Logging
   ══════════════════════════════════════════════════════════════════════════ */
async function loggingPage(guildId) {
  const path = `/api/guilds/${guildId}/settings/logging`;
  let data = await api.get(path);
  const host = h("div", { class: "stack" });
  const events = new Set(data.config.events || []);

  const saveEvents = () => api.patch(path, { events: [...events] });

  function paint() {
    fill(
      host,
      ui.card(
        {},
        ui.switchRow(
          "Write a log",
          data.config.channel_id ? null : "Pick a channel first.",
          data.config.enabled,
          async (value, input) => {
            try {
              await api.patch(path, { enabled: value });
              data.config.enabled = value;
            } catch (error) {
              input.checked = !value;
              throw error;
            }
          }
        ),
        channelPicker(guildId, data.config.channel_id, async (value) => {
          await api.patch(path, { channel_id: value });
          data.config.channel_id = value;
        })
      ),

      // Grouped rather than thirteen loose toggles: nobody reasons about
      // "nick_change" in isolation, they reason about "member stuff".
      ...data.meta.groups.map((group) => {
        const all = group.events.every((key) => events.has(key));
        const some = group.events.some((key) => events.has(key));
        return ui.card(
          {},
          ui.cardHead(group.label, {
            glyph: all ? "✅" : some ? "◐" : "○",
            sub: group.blurb,
            action: ui.actionButton(
              all ? "None" : "All",
              async () => {
                group.events.forEach((key) => (all ? events.delete(key) : events.add(key)));
                await saveEvents();
                paint();
              },
              { size: "sm" }
            ),
          }),
          h(
            "div",
            { class: "stack stack--tight" },
            ...group.events.map((key) =>
              ui.switchRow(
                data.meta.labels[key] || key,
                null,
                events.has(key),
                async (value) => {
                  if (value) events.add(key);
                  else events.delete(key);
                  await saveEvents();
                }
              )
            )
          )
        );
      })
    );
  }

  paint();
  return host;
}

/* ══════════════════════════════════════════════════════════════════════════
   Leveling
   ══════════════════════════════════════════════════════════════════════════ */
async function levelingPage(guildId) {
  const path = `/api/guilds/${guildId}/settings/leveling`;
  let data = await api.get(path);
  const host = h("div", { class: "stack" });
  const save = (patch) => api.patch(path, patch);

  async function reload() {
    data = await api.get(path, { fresh: true });
    paint();
  }

  function paint() {
    const config = data.config;
    const number = (key, label, hint, min, max) => {
      const input = h("input", {
        class: "input",
        type: "number",
        min: String(min),
        max: String(max),
        value: String(config[key]),
      });
      ui.autoSave(input, async (value) => {
        await save({ [key]: Number(value) });
        config[key] = Number(value);
      });
      return ui.field(label, input, { hint });
    };

    fill(
      host,
      ui.card(
        {},
        ui.switchRow("Server leveling", "Members earn XP from messages here.", config.enabled, (value) =>
          save({ enabled: value })
        )
      ),

      ui.card(
        {},
        ui.cardHead("XP rate", { glyph: "⚡" }),
        h("div", { class: "grid grid--2" },
          number("xp_min", "Minimum per message", "1–500", 1, 500),
          number("xp_max", "Maximum per message", "1–500", 1, 500)),
        number("cooldown", "Cooldown (seconds)", "How long before a member can earn again.", 0, 3600)
      ),

      ui.card(
        {},
        ui.cardHead("Announcements", { glyph: "📣" }),
        ui.switchRow("Announce level-ups here", null, config.announce, (value) => save({ announce: value })),
        channelPicker(guildId, config.announce_channel, (value) => save({ announce_channel: value }),
          "Unset means wherever they were talking."),
        h("hr", { style: { border: 0, borderTop: "1px solid var(--line)", margin: "var(--s-3) 0" } }),
        ui.switchRow(
          "Announce account level-ups here too",
          "The account-wide level follows members between servers. This only decides whether it's announced in this one.",
          config.global_announce,
          (value) => save({ global_announce: value })
        ),
        channelPicker(guildId, config.global_announce_channel, (value) => save({ global_announce_channel: value }),
          "Unset follows the channel above.")
      ),

      rewardsCard()
    );
  }

  function rewardsCard() {
    const level = h("input", { class: "input", type: "number", min: "1", max: "1000", value: "5" });
    const roleSelect = h("select", { class: "select" }, h("option", { value: "" }, "Loading roles…"));
    api.get(`/api/guilds/${guildId}/roles`).then(({ roles }) => {
      fill(
        roleSelect,
        h("option", { value: "" }, "Pick a role"),
        ...roles.map((role) =>
          h(
            "option",
            { value: role.id, disabled: !role.assignable },
            role.assignable ? role.name : `${role.name} — NanoBot can't assign this`
          )
        )
      );
    });

    return ui.card(
      {},
      ui.cardHead("Role rewards", { glyph: "🎭", sub: "Given automatically on level-up" }),
      data.rewards.length
        ? h(
            "div",
            { class: "stack stack--tight" },
            ...data.rewards.map((reward) =>
              h(
                "div",
                { class: "tile row" },
                ui.pill(`Level ${reward.level}`, "brand"),
                h("span", { class: "grow small" }, `<@&${reward.role_id}>`),
                ui.actionButton(
                  "Remove",
                  async () => {
                    const result = await api.patch(`${path}/rewards`, {
                      level: reward.level,
                      action: "remove",
                    });
                    data.rewards = result.rewards;
                    paint();
                  },
                  { size: "sm", variant: "danger" }
                )
              )
            )
          )
        : h("p", { class: "small dim" }, "No role rewards yet."),
      h(
        "div",
        { class: "row", style: { marginTop: "var(--s-3)" } },
        h("span", { style: { width: "100px" } }, level),
        h("span", { class: "grow" }, roleSelect),
        ui.actionButton(
          "Add",
          async () => {
            if (!roleSelect.value) {
              ui.warn("Pick a role first.");
              return;
            }
            const result = await api.patch(`${path}/rewards`, {
              level: Number(level.value),
              role_id: roleSelect.value,
            });
            data.rewards = result.rewards;
            paint();
          },
          { size: "sm", variant: "primary" }
        )
      )
    );
  }

  paint();
  return host;
}

/* ══════════════════════════════════════════════════════════════════════════
   Economy — the shop, and a clear line around what isn't yours to change
   ══════════════════════════════════════════════════════════════════════════ */
async function economyPage(guildId) {
  const path = `/api/guilds/${guildId}/settings/economy`;
  let data = await api.get(path);
  const host = h("div", { class: "stack" });

  async function reload() {
    data = await api.get(path, { fresh: true });
    paint();
  }

  function paint() {
    const config = data.config;
    const text = (key, label, hint) => {
      const input = h("input", { class: "input", value: config[key] });
      ui.autoSave(input, async (value) => {
        await api.patch(path, { [key]: value });
        config[key] = value;
      });
      return ui.field(label, input, { hint });
    };

    fill(
      host,
      ui.card(
        {},
        ui.cardHead("Your currency", { glyph: config.currency_emoji || "🪙" }),
        h("div", { class: "grid grid--2" },
          text("currency_name", "Name", "Singular — “s” is added automatically."),
          text("currency_emoji", "Emoji", "Shown next to every amount."))
      ),

      data.pending.length ? pendingCard() : null,
      shopCard(),

      ui.card(
        {},
        ui.cardHead("Bot-wide settings", { glyph: "🔒", sub: "Not yours to change — and that's on purpose" }),
        h("p", { class: "small muted" }, data.bot_wide.why),
        h(
          "div",
          { class: "grid grid--2" },
          ...Object.entries(data.bot_wide.rewards).map(([key, value]) =>
            ui.statTile(key.replace(/_/g, " "), fmt.num(value))
          )
        )
      )
    );
  }

  function pendingCard() {
    return ui.card(
      { class: "card--accent" },
      ui.cardHead("Waiting to be handed over", {
        glyph: "🎁",
        sub: `${fmt.plural(data.pending.length, "reward")} bought and unfulfilled`,
      }),
      h(
        "div",
        { class: "stack stack--tight" },
        ...data.pending.map((purchase) =>
          h(
            "div",
            { class: "tile row" },
            h(
              "div",
              { class: "grow" },
              h("strong", {}, purchase.item_name),
              h("div", { class: "tiny dim" }, `<@${purchase.user_id}> · ${fmt.relative(purchase.bought_at)}`)
            ),
            ui.actionButton(
              "Mark done",
              async () => {
                const result = await api.post(
                  `/api/guilds/${guildId}/shop/pending/${purchase.id}`
                );
                data.pending = result.pending;
                ui.ok("Marked as handed over.");
                paint();
              },
              { size: "sm", variant: "primary" }
            )
          )
        )
      )
    );
  }

  function shopCard() {
    return ui.card(
      {},
      ui.cardHead("Shop", {
        glyph: "🛒",
        sub: `${fmt.plural(data.shop.length, "reward")} on sale`,
        action: ui.actionButton("Add", openAddItem, { size: "sm", variant: "primary", glyph: "＋" }),
      }),
      data.shop.length
        ? h(
            "div",
            { class: "stack stack--tight" },
            ...data.shop.map((item) =>
              h(
                "div",
                { class: "tile row" },
                h("span", { class: "icon-tile" }, item.kind === "role" ? "🎭" : "🎁"),
                h(
                  "div",
                  { class: "grow" },
                  h("strong", {}, item.name),
                  h(
                    "div",
                    { class: "tiny dim" },
                    `${config_currency()} ${fmt.num(item.price)}` +
                      (item.stock >= 0 ? ` · ${fmt.num(item.stock)} left` : "") +
                      (item.per_user_limit ? ` · max ${item.per_user_limit} each` : "")
                  )
                ),
                ui.actionButton(
                  "Remove",
                  async () => {
                    const yes = await ui.confirm(
                      `Remove “${item.name}”?`,
                      "It disappears from the shop. Anything already bought stays bought.",
                      { danger: true, confirmLabel: "Remove it" }
                    );
                    if (!yes) return;
                    const result = await api.del(`/api/guilds/${guildId}/shop/items/${item.id}`);
                    data.shop = result.shop;
                    paint();
                  },
                  { size: "sm", variant: "danger" }
                )
              )
            )
          )
        : ui.empty("Nothing on sale", "Add a reward, or run /shop seed in Discord for a starter set.", {
            glyph: "🛒",
          })
    );
  }

  const config_currency = () => data.config.currency_emoji || "🪙";

  function openAddItem() {
    const name = h("input", { class: "input", placeholder: "e.g. Custom colour role" });
    const price = h("input", { class: "input", type: "number", min: "1", value: "1000" });
    const description = h("textarea", { class: "textarea", placeholder: "What does the member get?" });
    const kind = h(
      "select",
      { class: "select" },
      h("option", { value: "custom" }, "Custom — a mod hands it over"),
      h("option", { value: "role" }, "Role — NanoBot grants it instantly")
    );
    const roleSelect = h("select", { class: "select" }, h("option", { value: "" }, "Loading roles…"));
    const roleField = ui.field("Which role", roleSelect);
    roleField.style.display = "none";
    kind.addEventListener("change", () => {
      roleField.style.display = kind.value === "role" ? "" : "none";
    });
    api.get(`/api/guilds/${guildId}/roles`).then(({ roles }) => {
      fill(
        roleSelect,
        h("option", { value: "" }, "Pick a role"),
        ...roles.map((role) =>
          h(
            "option",
            { value: role.id, disabled: !role.assignable },
            role.assignable ? role.name : `${role.name} — NanoBot can't assign this`
          )
        )
      );
    });
    const stock = h("input", { class: "input", type: "number", min: "0", placeholder: "Unlimited" });
    const limit = h("input", { class: "input", type: "number", min: "1", placeholder: "No limit" });

    const view = ui.sheet(
      "Add a reward",
      h(
        "div",
        { class: "stack" },
        ui.field("Name", name),
        ui.field("Price", price, { hint: "In your server's currency." }),
        ui.field("Kind", kind),
        roleField,
        ui.field("Description", description),
        h("div", { class: "grid grid--2" },
          ui.field("Stock", stock, { hint: "Blank = unlimited" }),
          ui.field("Per member", limit, { hint: "Blank = no limit" }))
      ),
      {
        actions: [
          h("button", { class: "btn", type: "button", onClick: () => view.close() }, "Cancel"),
          ui.actionButton(
            "Add it",
            async () => {
              const result = await api.post(`/api/guilds/${guildId}/shop/items`, {
                name: name.value,
                price: Number(price.value),
                kind: kind.value,
                role_id: roleSelect.value || null,
                description: description.value,
                stock: stock.value === "" ? null : Number(stock.value),
                per_user_limit: limit.value === "" ? null : Number(limit.value),
              });
              data.shop = result.shop;
              view.close();
              ui.ok(`“${name.value}” is on sale.`);
              paint();
            },
            { variant: "primary" }
          ),
        ],
      }
    );
  }

  paint();
  return host;
}

/* ══════════════════════════════════════════════════════════════════════════
   Games
   ══════════════════════════════════════════════════════════════════════════ */
async function gamesPage(guildId) {
  const path = `/api/guilds/${guildId}/settings/games`;
  let data = await api.get(path);
  const host = h("div", { class: "stack" });

  const ACTIVITIES = [
    ["work", "💼", "Work", "Safe. Steady pay and a career ladder."],
    ["mine", "⛏️", "Mining", "Dig a vein of ore. Small cave-in risk."],
    ["hunt", "🏹", "Hunting", "Pelts, meat and rare trophies. Injury risk."],
    ["explore", "🧭", "Exploring", "Usually nothing, occasionally a lot."],
    ["rob", "🥷", "Robbing", "Members stealing from each other. Turn this off if it causes friction."],
  ];

  function paint() {
    fill(
      host,
      ui.card(
        {},
        ui.cardHead("Fishing", { glyph: "🎣" }),
        ui.switchRow("Fishing here", "The cast cooldown itself is bot-wide.", data.fishing.enabled, (value) =>
          api.patch(path, { fishing: { enabled: value } })
        )
      ),

      ui.card(
        {},
        ui.cardHead("Activities", { glyph: "🧭" }),
        h(
          "div",
          { class: "stack stack--tight" },
          ...ACTIVITIES.map(([key, glyph, label, blurb]) =>
            ui.switchRow(
              `${glyph}  ${label}`,
              blurb,
              data.activities[`${key}_enabled`],
              (value) => api.patch(path, { activities: { [`${key}_enabled`]: value } })
            )
          )
        ),
        h(
          "div",
          { class: "card__foot small dim" },
          "Cooldown lengths are bot-wide, because the claim they gate follows a " +
            "member between servers. Only the bot owner can change them (!cooldown)."
        )
      ),

      ui.card(
        {},
        ui.cardHead("Casino", { glyph: "🎰", sub: `Jackpot: ${fmt.num(data.casino.jackpot_pool)}` }),
        ui.switchRow("Casino here", null, data.casino.enabled, (value) =>
          api.patch(path, { casino: { enabled: value } })
        ),
        h("div", { class: "grid grid--2" }, betField("min_bet", "Minimum bet"), betField("max_bet", "Maximum bet"))
      )
    );
  }

  function betField(key, label) {
    const input = h("input", { class: "input", type: "number", min: "1", value: String(data.casino[key]) });
    ui.autoSave(input, async (value) => {
      await api.patch(path, { casino: { [key]: Number(value) } });
      data.casino[key] = Number(value);
    });
    return ui.field(label, input);
  }

  paint();
  return host;
}

/* ══════════════════════════════════════════════════════════════════════════
   Tickets
   ══════════════════════════════════════════════════════════════════════════ */
async function ticketsPage(guildId) {
  const path = `/api/guilds/${guildId}/settings/tickets`;
  let data = await api.get(path);
  const host = h("div", { class: "stack" });
  const save = (patch) => api.patch(path, patch);

  function paint() {
    const config = data.config;
    const title = h("input", {
      class: "input",
      value: config.panel_title || "",
      placeholder: "Need help?",
    });
    const message = h("textarea", {
      class: "textarea",
      value: config.panel_message || "",
      placeholder: "Press the button and tell us what's up.",
    });
    ui.autoSave(title, (value) => save({ panel_title: value }));
    ui.autoSave(message, (value) => save({ panel_message: value }));

    const cap = h("input", {
      class: "input tabular",
      type: "number",
      min: "1",
      max: "25",
      value: String(config.max_open),
    });
    ui.autoSave(cap, (value) => save({ max_open: Number(value) }));

    fill(
      host,
      ui.banner(data.meta.why, { kind: "info", glyph: "🎫" }),

      !data.meta.needs_threads
        ? ui.banner(
            "NanoBot is missing Create Private Threads here, so it can't open a " +
              "ticket at all.",
            { kind: "bad", glyph: "🔒" }
          )
        : null,
      !data.meta.can_lock
        ? ui.banner(
            "Without Manage Threads, a closed ticket is archived but not locked " +
              "— people can still reply to it.",
            { kind: "warn", glyph: "⚠️" }
          )
        : null,

      ui.card(
        {},
        ui.switchRow(
          "Tickets",
          config.staff_role_id ? null : "Pick a staff role first.",
          config.enabled,
          async (value, input) => {
            try {
              await save({ enabled: value });
              config.enabled = value;
            } catch (error) {
              input.checked = !value;
              throw error;
            }
          }
        )
      ),

      ui.card(
        {},
        ui.cardHead("Who handles them", { glyph: "👮" }),
        rolePicker(guildId, config.staff_role_id, (value) => save({ staff_role_id: value }),
          "Mentioned when a ticket opens, and allowed to claim and close."),
        channelPicker(guildId, config.log_channel_id, (value) => save({ log_channel_id: value }),
          "Where the transcript is posted when a ticket closes."),
        ui.field("Open tickets per member", cap, {
          hint: "Stops one person filling the staff channel.",
        })
      ),

      ui.card(
        {},
        ui.cardHead("The panel", { glyph: "🔘", sub: "The message members press to open a ticket" }),
        channelPicker(guildId, config.panel_channel_id, (value) => save({ panel_channel_id: value })),
        ui.field("Title", title),
        ui.field("Message", message),
        ui.banner(
          "Post the panel itself with /ticket panel in Discord — it has to be " +
            "sent as a message with a persistent button on it.",
          { kind: "info", glyph: "💡" }
        )
      )
    );
  }

  paint();
  return host;
}

/* ══════════════════════════════════════════════════════════════════════════
   Birthdays
   ══════════════════════════════════════════════════════════════════════════ */
async function birthdaysPage(guildId) {
  const path = `/api/guilds/${guildId}/settings/birthdays`;
  let data = await api.get(path);
  const host = h("div", { class: "stack" });
  const save = (patch) => api.patch(path, patch);

  function paint() {
    const config = data.config;
    const message = h("textarea", {
      class: "textarea",
      value: config.message || "",
      placeholder: "🎉 Happy birthday {user}!",
    });
    ui.autoSave(message, (value) => save({ message: value }));

    const timezone = h(
      "select",
      { class: "select" },
      ...data.meta.timezones.map((tz) =>
        h("option", { value: tz.value, selected: tz.value === config.timezone },
          `${tz.emoji || ""} ${tz.label}`.trim())
      )
    );
    ui.autoSave(timezone, (value) => save({ timezone: value }));

    const hour = h(
      "select",
      { class: "select" },
      ...Array.from({ length: 24 }, (_, i) =>
        h("option", { value: i, selected: i === config.hour }, `${String(i).padStart(2, "0")}:00`)
      )
    );
    ui.autoSave(hour, (value) => save({ hour: Number(value) }));

    fill(
      host,
      ui.banner(data.meta.why, { kind: "info", glyph: "🎂" }),

      ui.card(
        {},
        ui.switchRow(
          "Announce birthdays",
          config.channel_id ? null : "Pick a channel first.",
          config.enabled,
          async (value, input) => {
            try {
              await save({ enabled: value });
              config.enabled = value;
            } catch (error) {
              input.checked = !value;
              throw error;
            }
          }
        ),
        channelPicker(guildId, config.channel_id, (value) => save({ channel_id: value }))
      ),

      ui.card(
        {},
        ui.cardHead("When", { glyph: "🕘" }),
        ui.field("Timezone", timezone, {
          hint:
            data.meta.suggested_timezone && data.meta.suggested_timezone !== config.timezone
              ? `Your voice regions suggest ${data.meta.suggested_timezone}.`
              : "Named zones, so daylight saving handles itself.",
        }),
        ui.field("Hour", hour, { hint: "Local to the timezone above." })
      ),

      ui.card(
        {},
        ui.cardHead("The message", { glyph: "✍️" }),
        ui.field("Message", message, { hint: "Tap a variable to insert it." }),
        h(
          "div",
          { class: "row row--wrap" },
          ...data.meta.variables.map((variable) =>
            h(
              "button",
              {
                class: "var-token",
                type: "button",
                title: variable.means,
                onClick: () => {
                  const at = message.selectionStart ?? message.value.length;
                  message.value =
                    message.value.slice(0, at) + variable.token + message.value.slice(at);
                  message.dispatchEvent(new Event("input"));
                  message.focus();
                },
              },
              variable.token
            )
          )
        ),
        ui.switchRow("Include a festive GIF", "Checked for reachability first.", config.gif_enabled,
          (value) => save({ gif_enabled: value })),
        ui.switchRow("Ping them", null, config.ping_enabled, (value) => save({ ping_enabled: value })),
        ui.switchRow(
          "Sing in voice",
          "If they're in a voice channel that day, once. Needs FFmpeg and PyNaCl.",
          config.vc_enabled,
          (value) => save({ vc_enabled: value })
        )
      ),

      ui.card(
        {},
        ui.cardHead("Registered", {
          glyph: "📅",
          sub: `${fmt.plural(data.birthdays.length, "member")}`,
        }),
        data.birthdays.length
          ? h(
              "div",
              { class: "stack stack--tight" },
              ...data.birthdays.map((entry) =>
                h(
                  "div",
                  { class: "row" },
                  entry.avatar
                    ? h("img", { class: "avatar avatar--sm", src: entry.avatar, alt: "", loading: "lazy" })
                    : h("span", { class: "avatar avatar--sm" }),
                  h("span", { class: "grow" }, entry.name),
                  h("span", { class: "small dim" }, entry.date)
                )
              )
            )
          : h(
              "p",
              { class: "small dim", style: { marginBottom: 0 } },
              "Nobody has registered yet — members add themselves with /birthday set."
            )
      )
    );
  }

  paint();
  return host;
}

/* ══════════════════════════════════════════════════════════════════════════
   Gatekeeper
   ══════════════════════════════════════════════════════════════════════════ */
async function gatekeeperPage(guildId) {
  const path = `/api/guilds/${guildId}/settings/gatekeeper`;
  let data = await api.get(path);
  const host = h("div", { class: "stack" });
  const save = (patch) => api.patch(path, patch);

  const DURATIONS = [
    { value: 0, label: "No minimum" },
    { value: 86400, label: "1 day" },
    { value: 604800, label: "1 week" },
    { value: 2592000, label: "30 days" },
    { value: 7776000, label: "90 days" },
  ];

  function paint() {
    const config = data.config;

    const duration = (key, options, hint) => {
      const select = h(
        "select",
        { class: "select" },
        ...options.map((o) =>
          h("option", { value: o.value, selected: Number(config[key]) === o.value }, o.label)
        )
      );
      ui.autoSave(select, async (value) => {
        await save({ [key]: Number(value) });
        config[key] = Number(value);
      });
      return select;
    };

    const sensitivity = h("input", {
      class: "input tabular",
      type: "number",
      min: String(data.meta.sensitivity.min),
      max: String(data.meta.sensitivity.max),
      value: String(config.stock_threshold),
    });
    ui.autoSave(sensitivity, (value) => save({ stock_threshold: Number(value) }));

    fill(
      host,
      ui.banner(data.meta.why, { kind: "info", glyph: "🚧" }),
      !data.meta.role_ok
        ? ui.banner(
            `NanoBot can't apply @${data.meta.role_name} — its own role has to sit above it.`,
            { kind: "bad", glyph: "🔒" }
          )
        : null,

      ui.card(
        {},
        ui.switchRow(
          "Gatekeeper",
          config.mute_role_id ? null : "Pick the mute role first.",
          config.enabled,
          async (value, input) => {
            try {
              await save({ enabled: value });
              config.enabled = value;
            } catch (error) {
              input.checked = !value;
              throw error;
            }
          }
        ),
        rolePicker(guildId, config.mute_role_id, (value) => save({ mute_role_id: value }),
          "Applied to a held account. It should deny sending messages everywhere."),
        channelPicker(guildId, config.quarantine_channel_id, (value) => save({ quarantine_channel_id: value }),
          "Where the verification prompt goes when a DM won't reach them."),
        channelPicker(guildId, config.log_channel_id, (value) => save({ log_channel_id: value }),
          "Mutes, verifications and kicks are reported here.")
      ),

      ui.card(
        {},
        ui.cardHead("What gets held", { glyph: "🔍" }),
        ui.switchRow("Accounts that are too new", null, config.mute_new_accounts,
          (value) => save({ mute_new_accounts: value })),
        ui.field("Too new means younger than", duration("min_account_age", DURATIONS)),
        ui.switchRow("Accounts with Discord's default avatar", null, config.mute_default_avatar,
          (value) => save({ mute_default_avatar: value })),
        ui.switchRow(
          "Accounts using a known stock avatar",
          "Matched against a catalogue of avatars raid accounts reuse.",
          config.mute_stock_avatar,
          (value) => save({ mute_stock_avatar: value })
        ),
        ui.field("Avatar match sensitivity", sensitivity, {
          hint: data.meta.sensitivity.blurb,
        })
      ),

      ui.card(
        {},
        ui.cardHead("How the checks combine", { glyph: "🔀" }),
        h(
          "div",
          { class: "stack stack--tight" },
          ...data.meta.match_modes.map((mode) =>
            h(
              "button",
              {
                class: "tile row",
                type: "button",
                style: {
                  cursor: "pointer",
                  textAlign: "left",
                  borderColor: config.match_mode === mode.key ? "var(--brand)" : null,
                },
                onClick: async () => {
                  await save({ match_mode: mode.key });
                  config.match_mode = mode.key;
                  paint();
                },
              },
              h(
                "div",
                { class: "grow" },
                h("strong", {}, mode.label),
                h("div", { class: "tiny dim" }, mode.blurb)
              ),
              config.match_mode === mode.key ? ui.pill("On", "brand") : null
            )
          )
        )
      ),

      ui.card(
        {},
        ui.cardHead("Getting out", { glyph: "🔓" }),
        ui.switchRow("Let them verify themselves", "A math captcha behind a button.",
          config.verify_enabled, (value) => save({ verify_enabled: value })),
        ui.switchRow(
          "Release age-held accounts automatically",
          "Once the account is old enough, without them having to do anything.",
          config.age_unmute_enabled,
          (value) => save({ age_unmute_enabled: value })
        ),
        ui.field("Release at", duration("unmute_age", [
          { value: 604800, label: "1 week" },
          { value: 2592000, label: "30 days" },
          { value: 3024000, label: "35 days" },
          { value: 7776000, label: "90 days" },
        ])),
        ui.field("Kick if never verified after", duration("kick_timeout", [
          { value: 86400, label: "1 day" },
          { value: 259200, label: "3 days" },
          { value: 604800, label: "1 week" },
          { value: 1209600, label: "2 weeks" },
        ]))
      )
    );
  }

  paint();
  return host;
}

/* ══════════════════════════════════════════════════════════════════════════
   Music
   ══════════════════════════════════════════════════════════════════════════ */
async function musicPage(guildId) {
  const path = `/api/guilds/${guildId}/settings/music`;
  let data = await api.get(path);
  const host = h("div", { class: "stack" });

  async function reload() {
    data = await api.get(path, { fresh: true });
    paint();
  }

  function paint() {
    fill(
      host,
      ui.card(
        {},
        ui.switchRow(
          "Stay in voice (24/7)",
          "NanoBot keeps the connection open when the queue runs dry.",
          data.config.stay_connected,
          (value) => api.patch(path, { stay_connected: value })
        )
      ),

      listCard("Server playlist", "playlist", data.playlist, {
        glyph: "📻",
        sub: "Played by /music guildplay, and by autoplay when the queue empties",
        placeholder: "A YouTube or Spotify URL",
      }),
      listCard("Blocked songs", "songs", data.blocked_songs, {
        glyph: "🚫",
        sub: "Nobody can queue these here",
        placeholder: "A title or a URL",
      }),
      blockedUsersCard(),
      historyCard(),
      operatorCard()
    );
  }

  /* The playlist comes back as {url, title, added_by} rows while the block
     lists are plain strings. Both land here, so normalise to "what to show" and
     "what to send back" — printing the row itself rendered [object Object]. */
  const entryLabel = (value) =>
    typeof value === "string" ? value : value.title || value.url || "";
  const entryValue = (value) => (typeof value === "string" ? value : value.url);

  function listCard(title, which, values, { glyph, sub, placeholder }) {
    const input = h("input", { class: "input", placeholder, "aria-label": placeholder });
    return ui.card(
      {},
      ui.cardHead(title, { glyph, sub }),
      h(
        "div",
        { class: "row" },
        h("span", { class: "grow" }, input),
        ui.actionButton(
          "Add",
          async () => {
            const value = input.value.trim();
            if (!value) return;
            await api.patch(`${path}/lists/${which}`, { value, action: "add" });
            input.value = "";
            await reload();
          },
          { size: "sm", variant: "primary" }
        )
      ),
      values.length
        ? h(
            "div",
            { class: "stack stack--tight", style: { marginTop: "var(--s-3)" } },
            ...values.map((value) => {
              const label = entryLabel(value);
              const key = entryValue(value);
              const sameAsLabel = label === key;
              return h(
                "div",
                { class: "tile row" },
                h(
                  "div",
                  { class: "grow", style: { minWidth: 0 } },
                  h("div", { class: "small", style: { wordBreak: "break-all" } }, label),
                  // A titled entry keeps its URL underneath, so a playlist row
                  // still says which link it is.
                  sameAsLabel
                    ? null
                    : h("div", { class: "tiny dim", style: { wordBreak: "break-all" } }, key)
                ),
                ui.actionButton(
                  "✕",
                  async () => {
                    await api.patch(`${path}/lists/${which}`, { value: key, action: "remove" });
                    await reload();
                  },
                  { size: "sm", title: `Remove ${label}` }
                )
              );
            })
          )
        : h("p", { class: "small dim", style: { marginTop: "var(--s-3)", marginBottom: 0 } }, "Nothing here yet."),
      which === "playlist"
        ? h(
            "p",
            { class: "tiny dim", style: { marginTop: "var(--s-2)", marginBottom: 0 } },
            "Entries that fail to play are pruned automatically."
          )
        : null
    );
  }

  function blockedUsersCard() {
    return ui.card(
      {},
      ui.cardHead("Blocked from queueing", {
        glyph: "🙅",
        action: ui.actionButton("Add", pickUser, { size: "sm" }),
      }),
      data.blocked_users.length
        ? h(
            "div",
            { class: "stack stack--tight" },
            ...data.blocked_users.map((user) =>
              h(
                "div",
                { class: "tile row" },
                h("span", { class: "grow" }, user.name),
                ui.actionButton(
                  "Unblock",
                  async () => {
                    await api.patch(`${path}/lists/users`, { value: user.id, action: "remove" });
                    await reload();
                  },
                  { size: "sm" }
                )
              )
            )
          )
        : h("p", { class: "small dim", style: { marginBottom: 0 } }, "Nobody is blocked.")
    );
  }

  async function pickUser() {
    const { members } = await api.get(`/api/guilds/${guildId}/members`);
    const view = ui.sheet(
      "Block somebody from queueing",
      h(
        "div",
        { class: "stack stack--tight" },
        ...members.map((member) =>
          h(
            "div",
            { class: "row" },
            h("img", { class: "avatar avatar--sm", src: member.avatar, alt: "", loading: "lazy" }),
            h("span", { class: "grow" }, member.name),
            ui.actionButton(
              "Block",
              async () => {
                await api.patch(`${path}/lists/users`, { value: member.id, action: "add" });
                view.close();
                await reload();
              },
              { size: "sm", variant: "danger" }
            )
          )
        )
      )
    );
  }

  function historyCard() {
    if (!data.history.length) return null;
    return ui.card(
      {},
      ui.cardHead("Recently played", { glyph: "🕘" }),
      h(
        "div",
        { class: "stack stack--tight" },
        ...data.history.slice(0, 10).map((track) =>
          h(
            "div",
            { class: "row row--between small" },
            h("span", { class: "grow", style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
              track.title || track.url),
            track.played_at ? h("span", { class: "dim tiny" }, fmt.relative(track.played_at)) : null
          )
        )
      )
    );
  }

  function operatorCard() {
    return ui.card(
      {},
      ui.cardHead("Playback settings", { glyph: "🔒", sub: "The bot operator's, not a server's" }),
      h("p", { class: "small muted" }, data.operator.why),
      h(
        "div",
        { class: "stack stack--tight small" },
        ...Object.entries(data.operator.values).map(([key, value]) =>
          h(
            "div",
            { class: "row row--between" },
            h("span", { class: "muted" }, key.replace("music_", "").replace(/_/g, " ")),
            h("span", { class: "tabular" }, String(value ?? "—"))
          )
        )
      )
    );
  }

  paint();
  return host;
}

/* ══════════════════════════════════════════════════════════════════════════
   Shared: the role picker
   ══════════════════════════════════════════════════════════════════════════ */
/**
 * A role picker, with roles NanoBot couldn't apply listed but disabled and
 * carrying the reason — the same rule the channel picker follows. Hiding them
 * turns "why isn't my role here?" into a support question.
 */
function rolePicker(guildId, current, onPick, hint = null) {
  const select = h("select", { class: "select" }, h("option", {}, "Loading roles…"));
  api.get(`/api/guilds/${guildId}/roles`).then(({ roles }) => {
    fill(
      select,
      h("option", { value: "" }, "— none —"),
      ...roles.map((role) =>
        h(
          "option",
          {
            value: role.id,
            selected: String(current || "") === role.id,
            disabled: !role.assignable,
          },
          role.assignable ? role.name : `${role.name} — NanoBot can't apply this`
        )
      )
    );
    ui.autoSave(select, (value) => onPick(value || null));
  });
  return ui.field("Role", select, { hint });
}

/* ══════════════════════════════════════════════════════════════════════════
   Shared: the channel picker
   ══════════════════════════════════════════════════════════════════════════ */
/**
 * A channel picker rather than an id field — the same reason every Discord
 * option in this bot is an autocomplete. Channels NanoBot can't post in are
 * listed but disabled with the reason, because "why isn't my channel here?" is
 * worse than "here's why you can't pick it".
 */
function channelPicker(guildId, current, onPick, hint = null) {
  const select = h("select", { class: "select" }, h("option", {}, "Loading channels…"));
  const status = h("span", {});

  api.get(`/api/guilds/${guildId}/channels`).then(({ channels }) => {
    fill(
      select,
      h("option", { value: "" }, "— none —"),
      ...channels.map((channel) =>
        h(
          "option",
          {
            value: channel.id,
            selected: String(current || "") === channel.id,
            disabled: !channel.can_send,
          },
          channel.can_send
            ? `#${channel.name}${channel.category ? ` · ${channel.category}` : ""}`
            : `#${channel.name} — NanoBot can't post here`
        )
      )
    );
    ui.autoSave(select, (value) => onPick(value || null), { status });
  });

  return ui.field("Channel", select, { hint });
}
