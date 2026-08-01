/**
 * views/moderation.js
 * Warn, timeout, kick, ban, unban, purge — deliberately the soberest page here.
 *
 * The rest of the dashboard is built to reduce clicks. This one isn't. Every
 * destructive action opens a sheet that makes you pick a member, type a reason,
 * and read what will happen before the button is live; ban and purge add a
 * second confirmation on top. That friction is the feature.
 *
 * Three things it does that a chat command can't:
 *
 *   **It says what you can't do, and why.** Actions the bot or you lack the
 *   permission for are shown greyed with the reason, rather than hidden or —
 *   worse — offered and then refused.
 *   **It shows the history before the decision.** Picking somebody to warn
 *   loads their warning list first, because "third strike" is a different
 *   decision from "first".
 *   **It says where the record goes.** The reason lands in Discord's audit log
 *   naming the dashboard, and the action is echoed into the server's log
 *   channel, so nothing here is quieter than the same action from chat.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as ui from "../core/ui.js";

const TIMEOUTS = [
  { value: 60, label: "1 minute" },
  { value: 300, label: "5 minutes" },
  { value: 3600, label: "1 hour" },
  { value: 21600, label: "6 hours" },
  { value: 86400, label: "1 day" },
  { value: 604800, label: "1 week" },
];

export async function moderation({ guildId }) {
  const base = `/api/guilds/${guildId}/moderation`;
  const state = await api.get(base);
  const host = h("div", { class: "stack" });

  const spec = (key) => state.actions.find((a) => a.key === key);

  function paint() {
    fill(
      host,
      h(
        "div",
        {},
        h("h1", {}, "Moderation"),
        h("div", { class: "small dim" }, "The same actions as the Discord commands, same checks")
      ),

      ui.banner(state.safety, { kind: "info", glyph: "🛡️" }),

      ui.card(
        {},
        ui.cardHead("Actions", { glyph: "⚖️" }),
        h(
          "div",
          { class: "grid grid--2" },
          ...state.actions.map((action) =>
            h(
              "button",
              {
                class: "action-tile",
                type: "button",
                disabled: !action.available,
                onClick: () => open(action.key),
              },
              h(
                "span",
                { class: "action-tile__name" },
                action.label,
                action.destructive ? ui.pill("Destructive", "warn") : null
              ),
              h("span", { class: "tiny dim" }, action.available ? action.blurb : action.why_not)
            )
          )
        )
      ),

      thresholdsCard()
    );
  }

  function thresholdsCard() {
    const cfg = state.warn_config;
    if (!cfg.kick_at && !cfg.ban_at) {
      return ui.card(
        {},
        ui.cardHead("Warning thresholds", { glyph: "⚠️" }),
        h(
          "p",
          { class: "small muted", style: { marginBottom: 0 } },
          "No automatic escalation is set up. Warnings are recorded and nothing " +
            "else happens — set thresholds with /warn config in Discord."
        )
      );
    }
    return ui.card(
      {},
      ui.cardHead("Warning thresholds", { glyph: "⚠️", sub: "Applied automatically" }),
      h(
        "div",
        { class: "stack stack--tight small" },
        cfg.kick_at
          ? h("div", {}, `Kicked at ${fmt.plural(cfg.kick_at, "warning")}`)
          : null,
        cfg.ban_at ? h("div", {}, `Banned at ${fmt.plural(cfg.ban_at, "warning")}`) : null,
        h(
          "div",
          { class: "tiny dim" },
          "These fire from the warnings feature itself, so a warning issued here " +
            "escalates exactly as one issued in chat does."
        )
      )
    );
  }

  /* ── Opening an action ────────────────────────────────────────────────── */
  function open(action) {
    if (action === "purge") return openPurge();
    if (action === "unban") return openUnban();
    return openMemberAction(action);
  }

  /**
   * The member-target actions share one sheet: pick, read their history, give a
   * reason, confirm. Sharing it is what keeps the reason field mandatory on all
   * four rather than on the three somebody remembered.
   */
  async function openMemberAction(action) {
    const meta = spec(action);
    const { members } = await api.get(`/api/guilds/${guildId}/members`);
    let target = null;

    const reason = h("textarea", {
      class: "textarea",
      placeholder: "Why? This goes in the audit log.",
      maxlength: String(state.limits.reason_max),
      style: { minHeight: "70px" },
    });
    const seconds = h(
      "select",
      { class: "select" },
      ...TIMEOUTS.map((t) => h("option", { value: t.value, selected: t.value === 3600 }, t.label))
    );
    const deleteDays = h(
      "select",
      { class: "select" },
      h("option", { value: "0", selected: true }, "Keep their messages"),
      h("option", { value: "1" }, "Delete the last day"),
      h("option", { value: "7" }, "Delete the last week")
    );

    const body = h("div", { class: "stack" });
    const search = h("input", {
      class: "input",
      type: "search",
      placeholder: "Search members",
      "aria-label": "Search members",
      onInput: async () => {
        const found = await api.get(
          `/api/guilds/${guildId}/members?q=${encodeURIComponent(search.value)}`
        );
        fill(list, ...found.members.map(memberRow));
      },
    });
    const list = h("div", { class: "stack stack--tight" });
    const detail = h("div", {});

    function memberRow(member) {
      return h(
        "button",
        {
          class: "tile row",
          type: "button",
          style: { width: "100%", cursor: "pointer" },
          onClick: () => pick(member),
        },
        h("img", { class: "avatar avatar--sm", src: member.avatar, alt: "", loading: "lazy" }),
        h("span", { class: "grow", style: { textAlign: "left" } }, member.name),
        h("span", { class: "dim" }, "›")
      );
    }

    async function pick(member) {
      target = member;
      // The history *before* the decision: a third strike is a different call
      // from a first, and having to go and look it up is how it gets skipped.
      const history = await api
        .get(`${base}/warnings?member=${member.id}`)
        .catch(() => ({ warnings: [], count: 0 }));

      fill(
        detail,
        h(
          "div",
          { class: "stack" },
          h(
            "div",
            { class: "row" },
            h("img", { class: "avatar avatar--md", src: member.avatar, alt: "" }),
            h(
              "div",
              { class: "grow" },
              h("strong", {}, member.name),
              h("div", { class: "tiny dim" }, `@${member.handle}`)
            ),
            h(
              "button",
              { class: "btn btn--ghost btn--sm", type: "button", onClick: () => reset() },
              "Change"
            )
          ),
          history.count
            ? h(
                "details",
                { class: "tile" },
                h("summary", { style: { cursor: "pointer" } }, `${fmt.plural(history.count, "previous warning")}`),
                h(
                  "div",
                  { class: "stack stack--tight", style: { marginTop: "var(--s-2)" } },
                  ...history.warnings.slice(-5).reverse().map((w) =>
                    h(
                      "div",
                      { class: "tiny" },
                      h("strong", {}, w.reason),
                      h("span", { class: "dim" }, ` — ${w.by_name}`)
                    )
                  )
                )
              )
            : h("p", { class: "tiny dim" }, "No warnings on record."),
          action === "timeout" ? ui.field("How long", seconds) : null,
          action === "ban" ? ui.field("Their recent messages", deleteDays) : null,
          ui.field("Reason", reason, { hint: "Required. Written to the audit log." }),
          meta.destructive
            ? ui.banner(meta.blurb, { kind: action === "ban" ? "bad" : "warn", glyph: "⚠️" })
            : null
        )
      );
      fill(body, detail);
      view.close();
      openConfirmSheet();
    }

    function reset() {
      target = null;
      fill(body, search, list);
      view.close();
      openPickSheet();
    }

    let view;

    function openPickSheet() {
      fill(body, search, list);
      view = ui.sheet(`${meta.label} someone`, body);
      fill(list, ...members.map(memberRow));
    }

    function openConfirmSheet() {
      view = ui.sheet(`${meta.label} ${target.name}`, body, {
        actions: [
          h("button", { class: "btn", type: "button", onClick: () => view.close() }, "Cancel"),
          ui.actionButton(meta.label, submit, {
            variant: meta.destructive ? "danger" : "primary",
          }),
        ],
      });
    }

    async function submit() {
      if (!reason.value.trim()) {
        ui.warn("Give a reason first — it goes in the audit log.");
        return;
      }
      // Ban gets a second confirmation. It is the one action here that a member
      // cannot walk back from on their own.
      if (action === "ban") {
        const sure = await ui.confirm(
          `Ban ${target.name}?`,
          "They can't rejoin until somebody unbans them. This is written to the " +
            "audit log with your name on it.",
          { danger: true, confirmLabel: "Ban them" }
        );
        if (!sure) return;
      }
      const payload = { member: target.id, reason: reason.value.trim() };
      if (action === "timeout") payload.seconds = Number(seconds.value);
      if (action === "ban") payload.delete_days = Number(deleteDays.value);

      const result = await api.post(`${base}/${action}`, payload);
      view.close();
      if (action === "warn") {
        const near = nearThreshold(result);
        ui.ok(
          `${target.name} warned — that's ${fmt.plural(result.warnings, "warning")}.` +
            (near ? ` ${near}` : ""),
          { title: "⚠️ Warned", ms: near ? 7000 : 4200 }
        );
      } else {
        ui.ok(`${target.name} — done.`, { title: meta.label });
      }
    }

    openPickSheet();
  }

  function nearThreshold(result) {
    const { kick, ban } = result.thresholds || {};
    if (ban && result.warnings >= ban) return "They're at the ban threshold.";
    if (kick && result.warnings >= kick) return "They're at the kick threshold.";
    if (ban && result.warnings === ban - 1) return "One more and they're banned automatically.";
    if (kick && result.warnings === kick - 1) return "One more and they're kicked automatically.";
    return "";
  }

  /* ── Unban ────────────────────────────────────────────────────────────── */
  async function openUnban() {
    const { bans } = await api.get(`${base}/bans`);
    const reason = h("textarea", { class: "textarea", placeholder: "Why are they coming back?" });
    const list = h("div", { class: "stack stack--tight" });

    const view = ui.sheet(
      "Unban somebody",
      h(
        "div",
        { class: "stack" },
        ui.field("Reason", reason, { hint: "Required." }),
        bans.length
          ? list
          : ui.empty("Nobody is banned", "Nothing to undo here.", { glyph: "🎉" })
      )
    );

    fill(
      list,
      ...bans.map((entry) =>
        h(
          "div",
          { class: "tile row" },
          h("img", { class: "avatar avatar--sm", src: entry.avatar, alt: "", loading: "lazy" }),
          h(
            "div",
            { class: "grow" },
            h("div", {}, entry.name),
            entry.reason ? h("div", { class: "tiny dim" }, entry.reason) : null
          ),
          ui.actionButton(
            "Unban",
            async () => {
              if (!reason.value.trim()) {
                ui.warn("Give a reason first.");
                return;
              }
              await api.post(`${base}/unban`, {
                member: entry.id,
                reason: reason.value.trim(),
              });
              view.close();
              ui.ok(`${entry.name} can rejoin now.`);
            },
            { size: "sm" }
          )
        )
      )
    );
  }

  /* ── Purge ────────────────────────────────────────────────────────────── */
  async function openPurge() {
    const { channels } = await api.get(`/api/guilds/${guildId}/channels`);
    const channel = h(
      "select",
      { class: "select" },
      h("option", { value: "" }, "Pick a channel"),
      ...channels.map((c) =>
        h(
          "option",
          { value: c.id, disabled: !c.can_send },
          c.can_send ? `#${c.name}` : `#${c.name} — NanoBot can't act here`
        )
      )
    );
    const amount = h("input", {
      class: "input tabular",
      type: "number",
      min: "1",
      max: String(state.limits.purge_max),
      value: "20",
    });
    const only = h(
      "select",
      { class: "select" },
      ...state.purge_filters.map((f) => h("option", { value: f.key }, f.label))
    );
    const reason = h("textarea", { class: "textarea", placeholder: "Why?" });

    const view = ui.sheet(
      "Purge messages",
      h(
        "div",
        { class: "stack" },
        ui.banner(
          "Deleted messages cannot be recovered. Discord's bulk delete only " +
            "reaches the last 14 days — anything older is skipped rather than " +
            "deleted one at a time.",
          { kind: "bad", glyph: "⚠️" }
        ),
        ui.field("Channel", channel),
        ui.field("How many", amount, {
          hint: `Up to ${state.limits.purge_max} — Discord's limit for a bulk delete.`,
        }),
        ui.field("Whose messages", only),
        ui.field("Reason", reason, { hint: "Required." }),
        h(
          "p",
          { class: "tiny dim" },
          "Need to go further back, or delete more? /purge mode:slow in Discord " +
            "does up to 500 at any age behind a typed confirmation code — which " +
            "is not something to put on a web form."
        )
      ),
      {
        actions: [
          h("button", { class: "btn", type: "button", onClick: () => view.close() }, "Cancel"),
          ui.actionButton(
            "Delete them",
            async () => {
              if (!channel.value) {
                ui.warn("Pick a channel first.");
                return;
              }
              if (!reason.value.trim()) {
                ui.warn("Give a reason first.");
                return;
              }
              const target = channels.find((c) => c.id === channel.value);
              const sure = await ui.confirm(
                `Delete ${amount.value} messages in #${target.name}?`,
                "This can't be undone, and there is no copy anywhere.",
                { danger: true, confirmLabel: "Delete them" }
              );
              if (!sure) return;
              const result = await api.post(`${base}/purge`, {
                channel_id: channel.value,
                amount: Number(amount.value),
                only: only.value,
                reason: reason.value.trim(),
              });
              view.close();
              ui.ok(
                `Deleted ${fmt.plural(result.deleted, "message")} in #${result.channel.name}.` +
                  (result.note ? ` ${result.note}` : ""),
                { title: "🗑️ Purged", ms: result.note ? 7000 : 4200 }
              );
            },
            { variant: "danger" }
          ),
        ],
      }
    );
  }

  paint();
  return host;
}
