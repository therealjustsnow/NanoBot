/**
 * views/login.js
 * The sign-in screen — and, when the instance isn't finished being set up, the
 * page that says exactly what is missing.
 *
 * That second job is the reason this screen has any logic at all. The most
 * common way a self-hosted dashboard fails is a redirect URI that doesn't match
 * the one in the Discord developer portal, and the default experience of that is
 * a blank error page. Here the failure names the config key and shows the exact
 * URL to paste.
 */

import { h } from "../core/dom.js";
import * as store from "../core/store.js";
import { apiUrl, deploymentLabel, isCrossOrigin } from "../core/config.js";

const REASONS = {
  denied: "You cancelled the Discord sign-in. Nothing was shared.",
  state: "That sign-in took too long and expired. Try again — it should be quick.",
  nocode: "Discord didn't send an authorisation code back. Try again.",
  exchange:
    "Discord rejected the sign-in. The redirect URL in config.ini has to match " +
    "the one registered in the developer portal exactly, including https and " +
    "any port.",
  identity: "Discord wouldn't tell us who you are. Try again in a moment.",
};

export function login() {
  const params = new URLSearchParams(location.search);
  const reason = params.get("error");
  const { configured, missing, bot } = store.state;

  return h(
    "div",
    { class: "login" },
    h(
      "div",
      { class: "login__card stack" },
      h(
        "div",
        { class: "card stack" },
        bot?.avatar
          ? h("img", {
              class: "avatar avatar--lg",
              src: bot.avatar,
              alt: "",
              style: { margin: "0 auto" },
            })
          : h("div", { class: "login__mark" }, "🤖"),
        h("h1", {}, bot?.name || "NanoBot"),
        h(
          "p",
          { class: "muted" },
          "Configure your server and play the whole economy from your phone. " +
            "Everything here is the same account you use in Discord."
        ),

        reason && h("div", { class: "banner banner--warn" },
          h("span", { class: "banner__glyph" }, "⚠️"),
          h("span", { class: "grow" }, REASONS[reason] || "That sign-in didn't work. Try again.")),

        configured
          ? h(
              "a",
              {
                class: "btn btn--primary btn--lg btn--block",
                // Absolute when the API is elsewhere: this is a real navigation
                // to Discord's consent screen, so it has to leave for the host
                // that holds the client secret.
                href: apiUrl("/api/auth/login"),
              },
              "Continue with Discord"
            )
          : setupNeeded(missing),

        h(
          "p",
          { class: "tiny dim" },
          "NanoBot asks Discord only for your name and your server list. " +
            "It never posts as you and can't add you to servers."
        ),
        isCrossOrigin()
          ? h(
              "p",
              { class: "tiny dim" },
              "Connected to ",
              h("code", {}, deploymentLabel() || "a separate API host"),
              "."
            )
          : null
      ),
      h(
        "p",
        { class: "tiny dim center" },
        "Prefer commands? Everything on this dashboard is also a Discord command — start with ",
        h("code", {}, "/help"),
        "."
      )
    )
  );
}

function setupNeeded(missing) {
  const origin = location.origin;
  return h(
    "div",
    { class: "stack", style: { textAlign: "left" } },
    h(
      "div",
      { class: "banner banner--bad" },
      h("span", { class: "banner__glyph" }, "🔧"),
      h(
        "span",
        { class: "grow" },
        h("strong", {}, "This dashboard isn't set up yet."),
        h(
          "div",
          { class: "small" },
          "Add these to the ",
          h("code", {}, "[dashboard]"),
          " section of ",
          h("code", {}, "config.ini"),
          ", then restart the bot."
        )
      )
    ),
    h(
      "ul",
      { class: "stack stack--tight small", style: { paddingLeft: "1.1rem" } },
      ...missing.map((key) =>
        h(
          "li",
          {},
          h("code", {}, key),
          key === "dashboard_base_url"
            ? h("div", { class: "dim tiny" }, `Probably: ${origin}`)
            : key === "dashboard_client_secret"
              ? h("div", { class: "dim tiny" }, "Developer portal → OAuth2 → Client Secret")
              : null
        )
      )
    ),
    h(
      "div",
      { class: "tile stack stack--tight" },
      h("div", { class: "small" }, h("strong", {}, "Redirect URL to register")),
      h(
        "code",
        { class: "tiny", style: { wordBreak: "break-all" } },
        `${origin}/api/auth/callback`
      ),
      h(
        "div",
        { class: "tiny dim" },
        "Paste this into Developer Portal → OAuth2 → Redirects. It has to match " +
          "character for character."
      )
    )
  );
}
