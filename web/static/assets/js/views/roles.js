/**
 * views/roles.js
 * Self-role panels, with the ordering you can only really do by dragging.
 *
 * This is the page that most justifies the dashboard existing. A panel's buttons
 * are laid out in one dimension, and in Discord the only way to move one was to
 * remove it and add it back — losing its label, emoji and style on the way.
 * Dragging is the obvious answer and a chat client can't offer it.
 *
 * The dragging is Pointer Events rather than HTML5 drag-and-drop, for one
 * reason: HTML5 DnD does not work on touch, and this bot's whole premise is
 * mobile. Pointer Events are one code path for finger, mouse and stylus.
 * `touch-action: none` on the row is what stops the page scrolling out from
 * under a drag.
 *
 * Keyboard users get the same power without the pointer: the grip is a real
 * button, and ↑/↓ move a row. A reorder anybody can *see* but only some people
 * can *do* would be worse than a plain list.
 *
 * Every reorder saves the whole order in one PATCH and re-renders the posted
 * message, so what's in the channel is never a stale version of what's here.
 */

import { h, fill } from "../core/dom.js";
import * as api from "../core/api.js";
import * as fmt from "../core/format.js";
import * as ui from "../core/ui.js";

export async function roles({ guildId }) {
  const base = `/api/guilds/${guildId}/roles/panels`;
  let data = await api.get(base);
  let roleList = [];
  const host = h("div", { class: "stack" });

  api.get(`/api/guilds/${guildId}/roles`).then(({ roles }) => (roleList = roles));

  const refresh = (panels) => {
    data = { ...data, panels };
    paint();
  };

  function paint() {
    fill(
      host,
      h(
        "div",
        { class: "row row--between" },
        h(
          "div",
          {},
          h("h1", {}, "Self roles"),
          h("div", { class: "small dim" }, "Buttons members tap to give themselves a role")
        ),
        ui.actionButton("New panel", newPanel, { variant: "primary", glyph: "＋", size: "sm" })
      ),

      data.panels.length
        ? h("div", { class: "stack" }, ...data.panels.map(panelCard))
        : ui.empty(
            "No panels yet",
            "A panel is a message with buttons on it. Make one, add some roles, " +
              "then post it wherever members will find it.",
            { glyph: "🎭" }
          )
    );
  }

  /* ── One panel ────────────────────────────────────────────────────────── */
  function panelCard(panel) {
    const mode = data.meta.modes.find((m) => m.key === panel.mode);
    return ui.card(
      { class: panel.problems.length ? "" : "card--accent" },
      ui.cardHead(panel.title, {
        glyph: "🎭",
        sub: `${mode?.label || panel.mode} · ${fmt.plural(panel.entries.length, "role")}` +
          (panel.posted ? ` · posted in #${panel.channel?.name ?? "a channel"}` : " · not posted yet"),
        action: h(
          "div",
          { class: "btn-group" },
          ui.actionButton("Edit", () => editPanel(panel), { size: "sm" }),
          ui.actionButton(panel.posted ? "Repost" : "Post", () => publish(panel), {
            size: "sm",
            variant: "primary",
            disabled: !panel.entries.length,
          })
        ),
      }),
      panel.description ? h("p", { class: "small muted" }, panel.description) : null,

      panel.problems.length
        ? ui.banner(
            h(
              "span",
              {},
              h("strong", {}, "This panel won't work as-is"),
              h("ul", { style: { margin: "4px 0 0", paddingLeft: "1.1rem" } },
                ...panel.problems.map((problem) => h("li", { class: "small" }, problem)))
            ),
            { kind: "bad", glyph: "⚠️" }
          )
        : null,

      sortable(panel),

      h(
        "div",
        { class: "row", style: { marginTop: "var(--s-3)" } },
        ui.actionButton("Add a role", () => addRole(panel), {
          size: "sm",
          glyph: "＋",
          disabled: panel.entries.length >= data.meta.max_entries,
        }),
        h("span", { class: "grow" }),
        ui.actionButton("Delete panel", () => deletePanel(panel), {
          size: "sm",
          variant: "danger",
        })
      ),
      panel.entries.length >= data.meta.max_entries
        ? h("p", { class: "tiny dim" },
            `A panel holds ${data.meta.max_entries} roles — Discord's limit, not ours.`)
        : null
    );
  }

  /* ── The sortable list ────────────────────────────────────────────────── */
  function sortable(panel) {
    const list = h("div", { class: "sortable" });
    let order = panel.entries.map((entry) => entry.role_id);

    const save = async () => {
      try {
        const result = await api.patch(`${base}/${panel.id}/order`, { order });
        refresh(result.panels);
        ui.ok("Order saved.", { ms: 1800 });
      } catch (error) {
        ui.reportError(error);
        paint(); // put the list back the way the server has it
      }
    };

    const move = (from, to) => {
      if (to < 0 || to >= order.length || from === to) return;
      const [id] = order.splice(from, 1);
      order.splice(to, 0, id);
      panel.entries = order.map((rid) => panel.entries.find((e) => e.role_id === rid));
      render();
      save();
    };

    function render() {
      fill(
        list,
        ...panel.entries.map((entry, index) => row(entry, index)),
        panel.entries.length
          ? null
          : h("p", { class: "small dim" }, "No roles on this panel yet.")
      );
    }

    function row(entry, index) {
      const grip = h(
        "button",
        {
          class: "sortable__grip btn btn--ghost btn--sm",
          type: "button",
          "aria-label": `${entry.label}, position ${index + 1} of ${panel.entries.length}. Use arrow keys to move.`,
          onKeydown: (event) => {
            if (event.key === "ArrowUp") {
              event.preventDefault();
              move(index, index - 1);
            } else if (event.key === "ArrowDown") {
              event.preventDefault();
              move(index, index + 1);
            }
          },
        },
        "⠿"
      );

      const node = h(
        "div",
        { class: "sortable__item", dataset: { index: String(index) } },
        grip,
        entry.color
          ? h("span", {
              style: {
                width: "10px",
                height: "10px",
                borderRadius: "50%",
                background: entry.color,
                flex: "0 0 auto",
              },
            })
          : null,
        entry.emoji ? h("span", { "aria-hidden": "true" }, entry.emoji) : null,
        h(
          "div",
          { class: "grow" },
          h("div", {}, entry.label),
          h(
            "div",
            { class: "tiny dim" },
            entry.deleted
              ? "This role was deleted"
              : entry.assignable
                ? `@${entry.name}`
                : `@${entry.name} — NanoBot can't assign this`
          )
        ),
        !entry.assignable ? ui.pill(entry.deleted ? "Gone" : "Blocked", "bad") : null,
        h(
          "button",
          {
            class: "btn btn--ghost btn--sm",
            type: "button",
            "aria-label": `Remove ${entry.label}`,
            onClick: async () => {
              const result = await api.del(
                `${base}/${panel.id}/entries/${entry.role_id}`
              );
              refresh(result.panels);
            },
          },
          "✕"
        )
      );

      // Pointer-based dragging: one code path for finger, mouse and stylus.
      // HTML5 drag-and-drop simply doesn't fire on touch, which would make this
      // a desktop-only feature in a mobile-first product.
      grip.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        grip.setPointerCapture(event.pointerId);
        node.classList.add("sortable__item--dragging");
        const startY = event.clientY;
        let target = index;

        const onMove = (moveEvent) => {
          const rowHeight = node.offsetHeight + 8; // + the list's gap
          const delta = Math.round((moveEvent.clientY - startY) / rowHeight);
          const next = Math.max(0, Math.min(panel.entries.length - 1, index + delta));
          if (next === target) return;
          target = next;
          [...list.children].forEach((child, i) =>
            child.classList?.toggle("sortable__item--over", i === target && i !== index)
          );
        };
        const onUp = () => {
          grip.removeEventListener("pointermove", onMove);
          grip.removeEventListener("pointerup", onUp);
          grip.removeEventListener("pointercancel", onUp);
          node.classList.remove("sortable__item--dragging");
          [...list.children].forEach((child) =>
            child.classList?.remove("sortable__item--over")
          );
          if (target !== index) move(index, target);
        };
        grip.addEventListener("pointermove", onMove);
        grip.addEventListener("pointerup", onUp);
        grip.addEventListener("pointercancel", onUp);
      });

      return node;
    }

    render();
    return list;
  }

  /* ── Sheets ───────────────────────────────────────────────────────────── */
  function newPanel() {
    const title = h("input", { class: "input", placeholder: "e.g. Pick your colour" });
    const description = h("textarea", { class: "textarea", placeholder: "Optional. Shown above the buttons." });
    const mode = h(
      "select",
      { class: "select" },
      ...data.meta.modes.map((m) => h("option", { value: m.key }, `${m.label} — ${m.blurb}`))
    );
    const view = ui.sheet(
      "New panel",
      h("div", { class: "stack" },
        ui.field("Title", title),
        ui.field("Description", description),
        ui.field("How members pick", mode)),
      {
        actions: [
          h("button", { class: "btn", type: "button", onClick: () => view.close() }, "Cancel"),
          ui.actionButton(
            "Create",
            async () => {
              const result = await api.post(base, {
                title: title.value,
                description: description.value,
                mode: mode.value,
              });
              view.close();
              refresh(result.panels);
            },
            { variant: "primary" }
          ),
        ],
      }
    );
  }

  function editPanel(panel) {
    const title = h("input", { class: "input", value: panel.title });
    const description = h("textarea", { class: "textarea", value: panel.description || "" });
    const mode = h(
      "select",
      { class: "select" },
      ...data.meta.modes.map((m) =>
        h("option", { value: m.key, selected: m.key === panel.mode }, `${m.label} — ${m.blurb}`)
      )
    );
    const view = ui.sheet(
      "Edit panel",
      h("div", { class: "stack" },
        ui.field("Title", title),
        ui.field("Description", description),
        ui.field("How members pick", mode),
        panel.posted
          ? ui.banner("The posted message updates as soon as you save.", { kind: "info", glyph: "🔄" })
          : null),
      {
        actions: [
          h("button", { class: "btn", type: "button", onClick: () => view.close() }, "Cancel"),
          ui.actionButton(
            "Save",
            async () => {
              const result = await api.patch(`${base}/${panel.id}`, {
                title: title.value,
                description: description.value,
                mode: mode.value,
              });
              view.close();
              refresh(result.panels);
            },
            { variant: "primary" }
          ),
        ],
      }
    );
  }

  function addRole(panel) {
    const taken = new Set(panel.entries.map((e) => e.role_id));
    const select = h(
      "select",
      { class: "select" },
      h("option", { value: "" }, "Pick a role"),
      ...roleList
        .filter((role) => !taken.has(role.id))
        .map((role) =>
          h(
            "option",
            { value: role.id, disabled: !role.assignable },
            role.assignable ? role.name : `${role.name} — NanoBot can't assign this`
          )
        )
    );
    const label = h("input", { class: "input", placeholder: "Button text (defaults to the role name)" });
    const emoji = h("input", { class: "input", placeholder: "Optional emoji" });
    const style = h(
      "select",
      { class: "select" },
      ...data.meta.styles.map((s) => h("option", { value: s, selected: s === "secondary" }, s))
    );

    const view = ui.sheet(
      "Add a role",
      h("div", { class: "stack" },
        ui.field("Role", select),
        ui.field("Button text", label),
        ui.field("Emoji", emoji),
        ui.field("Button colour", style)),
      {
        actions: [
          h("button", { class: "btn", type: "button", onClick: () => view.close() }, "Cancel"),
          ui.actionButton(
            "Add",
            async () => {
              if (!select.value) {
                ui.warn("Pick a role first.");
                return;
              }
              const result = await api.post(`${base}/${panel.id}/entries`, {
                role_id: select.value,
                label: label.value,
                emoji: emoji.value,
                style: style.value,
              });
              view.close();
              refresh(result.panels);
            },
            { variant: "primary" }
          ),
        ],
      }
    );
  }

  async function publish(panel) {
    const { channels } = await api.get(`/api/guilds/${guildId}/channels`);
    const select = h(
      "select",
      { class: "select" },
      h("option", { value: "" }, "Pick a channel"),
      ...channels.map((channel) =>
        h(
          "option",
          {
            value: channel.id,
            disabled: !channel.can_send,
            selected: panel.channel?.id === channel.id,
          },
          channel.can_send ? `#${channel.name}` : `#${channel.name} — NanoBot can't post here`
        )
      )
    );
    const view = ui.sheet(
      panel.posted ? "Post it again" : "Post the panel",
      h("div", { class: "stack" },
        ui.field("Channel", select),
        panel.posted
          ? ui.banner(
              "This posts a fresh message. The old one stops being the panel — " +
                "delete it yourself if you don't want two.",
              { kind: "warn", glyph: "⚠️" }
            )
          : null),
      {
        actions: [
          h("button", { class: "btn", type: "button", onClick: () => view.close() }, "Cancel"),
          ui.actionButton(
            "Post it",
            async () => {
              if (!select.value) {
                ui.warn("Pick a channel first.");
                return;
              }
              const result = await api.post(`${base}/${panel.id}/publish`, {
                channel_id: select.value,
              });
              view.close();
              refresh(result.panels);
              ui.ok("Panel posted.", {
                action: result.jump_url
                  ? { label: "Open", onClick: () => window.open(result.jump_url, "_blank") }
                  : null,
              });
            },
            { variant: "primary" }
          ),
        ],
      }
    );
  }

  async function deletePanel(panel) {
    const yes = await ui.confirm(
      `Delete “${panel.title}”?`,
      "The panel and its buttons go. Any message already posted stays in the " +
        "channel but stops working — delete that yourself.",
      { danger: true, confirmLabel: "Delete it" }
    );
    if (!yes) return;
    const result = await api.del(`${base}/${panel.id}`);
    refresh(result.panels);
  }

  paint();
  return host;
}
