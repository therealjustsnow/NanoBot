# Wiki source

This folder is the source of truth for the [GitHub wiki](https://github.com/therealjustsnow/NanoBot/wiki). The `publish-wiki.yml` workflow force-pushes its contents to the wiki on every push to `main` that touches `wiki/**`.

- Edit pages here, in the repo — direct wiki edits get overwritten on the next sync.
- `Home.md` is the wiki landing page; `_Sidebar.md` / `_Footer.md` control wiki navigation chrome.
- Page links use wiki syntax: `[Setup](Setup)` links to `Setup.md`.
- This `README.md` is excluded from the sync.

**One-time setup:** GitHub only creates the underlying wiki git repository after the first page is created by hand. Before the workflow can push, go to the repo's **Wiki** tab → **Create the first page** → save anything (it will be overwritten). The wiki must also be enabled under **Settings → Features → Wikis**.

Content originally adapted from the [portfolio docs site](https://github.com/therealjustsnow/portfolio-/tree/main/docs).
