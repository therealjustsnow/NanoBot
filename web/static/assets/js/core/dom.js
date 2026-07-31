/**
 * core/dom.js
 * A hyperscript helper, and nothing else.
 *
 * There is no framework here on purpose. The dashboard's Content-Security-Policy
 * forbids external script hosts and inline script, so a CDN build is out; and a
 * bundled framework would mean a build step, which would mean the files served
 * are no longer the files in the repository. What is actually needed is a
 * readable way to build DOM and a way to swap a subtree — about eighty lines.
 *
 * `h(tag, props, ...children)` sets properties rather than attributes wherever
 * a property exists (`className`, `value`, `checked`), which is what makes
 * re-rendering an input not lose its state. Text children are set as
 * `textContent`, never `innerHTML`: every string on this page ultimately comes
 * from a server name, a member's nickname or an admin's own message template,
 * and none of those are trusted markup.
 */

/** Create an element. `props` may carry `class`, `dataset`, `style`, `on*`. */
export function h(tag, props = null, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class" || key === "className") {
        el.className = Array.isArray(value) ? value.filter(Boolean).join(" ") : value;
      } else if (key === "style" && typeof value === "object") {
        Object.assign(el.style, value);
      } else if (key === "dataset" && typeof value === "object") {
        Object.assign(el.dataset, value);
      } else if (key.startsWith("on") && typeof value === "function") {
        el.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === "html") {
        // Deliberately rare and deliberately loud. Only ever used for markup
        // this file itself produced — never for anything from the API.
        el.innerHTML = value;
      } else if (key in el && key !== "list" && key !== "form") {
        el[key] = value;
      } else {
        el.setAttribute(key, value === true ? "" : value);
      }
    }
  }
  append(el, children);
  return el;
}

function append(parent, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) {
      append(parent, child);
    } else if (child instanceof Node) {
      parent.appendChild(child);
    } else {
      parent.appendChild(document.createTextNode(String(child)));
    }
  }
}

/** Replace everything inside `node` with `children`. */
export function fill(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

/** A document fragment, for returning several siblings from one function. */
export function frag(...children) {
  const f = document.createDocumentFragment();
  append(f, children);
  return f;
}

export const $ = (selector, scope = document) => scope.querySelector(selector);
export const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];

/**
 * Swap one subtree for another with a short cross-fade.
 *
 * View transitions where the browser has them, a class-based fade where it
 * doesn't, and neither under `prefers-reduced-motion` — the swap still happens,
 * it just happens instantly.
 */
export function swap(host, next) {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !document.startViewTransition) {
    fill(host, next);
    return;
  }
  document.startViewTransition(() => fill(host, next));
}

/** Focus the first heading of a freshly-rendered view, for screen readers. */
export function announceView(host) {
  const heading = host.querySelector("h1, h2, [data-autofocus]");
  if (heading) {
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
  }
}
