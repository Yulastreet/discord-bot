"""Components V2 builders.

Replaces classic embeds. The API deliberately mirrors ``discord.Embed`` so the
migration stays mechanical: ``title`` / ``description`` / ``field`` /
``thumbnail`` / ``image`` / ``footer``.

Design choices:
- **No accent bar.** ``Container`` is always built with ``accent_colour=None``
  (the library default), which is the look we want. Embed colours are dropped.
- V2 caps a message at 4000 display characters and 40 components; ``Panel``
  guards both and truncates instead of letting Discord reject the message.
- A V2 message cannot carry ``content`` or ``embeds``. Anything that used to go
  in ``content`` (role pings...) must be passed through ``Panel.text()`` and the
  send call keeps its ``allowed_mentions``.
"""
from __future__ import annotations

import discord
from discord import ui

MAX_CHARS = 4000
MAX_COMPONENTS = 40
_TRUNC = "\n-# ... (truncated)"


def _md_escape_none(s):
    return s if s is not None else ""


class Panel:
    """Builds a Components V2 ``LayoutView`` with an embed-like API."""

    def __init__(self, title=None, description=None, *, spoiler=False):
        self._blocks: list = []          # (kind, payload)
        self._accessory = None           # Thumbnail attached to the first block
        self._images: list[str] = []
        self._footer = None
        self._spoiler = spoiler
        if title:
            self._blocks.append(("text", f"## {title}"))
        if description:
            self._blocks.append(("text", description))

    # ----- embed-like API -----

    def text(self, content):
        """Free-form block (also used for pings that used to live in `content`)."""
        if content:
            self._blocks.append(("text", str(content)))
        return self

    def field(self, name, value, inline=False):
        """Mirrors ``Embed.add_field``. V2 has no inline concept: inline fields
        are merged into a single line separated by ' · ' to stay compact."""
        name = _md_escape_none(name)
        value = _md_escape_none(value)
        if inline and self._blocks and self._blocks[-1][0] == "inline":
            self._blocks[-1][1].append((name, value))
        elif inline:
            self._blocks.append(("inline", [(name, value)]))
        else:
            self._blocks.append(("text", f"**{name}**\n{value}" if name else str(value)))
        return self

    def thumbnail(self, url):
        if url:
            self._accessory = str(url)
        return self

    def image(self, url):
        if url:
            self._images.append(str(url))
        return self

    def footer(self, text):
        if text:
            self._footer = str(text)
        return self

    def separator(self):
        self._blocks.append(("sep", None))
        return self

    # ----- rendering -----

    def _rendered_blocks(self) -> list:
        out = []
        for kind, payload in self._blocks:
            if kind == "text":
                out.append(("text", payload))
            elif kind == "inline":
                joined = "  ·  ".join(
                    f"**{n}** {v}" if n else str(v) for n, v in payload
                )
                out.append(("text", joined))
            elif kind == "sep":
                out.append(("sep", None))
        return out

    def _budget(self, blocks):
        """Enforce the 4000-char cap, truncating the tail rather than failing."""
        total = len(self._footer or "")
        kept = []
        for kind, payload in blocks:
            if kind != "text":
                kept.append((kind, payload)); continue
            if total + len(payload) <= MAX_CHARS - len(_TRUNC):
                kept.append((kind, payload)); total += len(payload)
            else:
                room = MAX_CHARS - len(_TRUNC) - total
                if room > 40:
                    kept.append(("text", payload[:room] + _TRUNC))
                    total = MAX_CHARS
                break
        return kept

    def container(self) -> ui.Container:
        blocks = self._budget(self._rendered_blocks())
        items: list = []
        for i, (kind, payload) in enumerate(blocks):
            if kind == "sep":
                items.append(ui.Separator())
                continue
            td = ui.TextDisplay(payload)
            # The thumbnail rides on the first text block, like an embed thumbnail.
            if i == 0 and self._accessory:
                items.append(ui.Section(td, accessory=ui.Thumbnail(self._accessory)))
            else:
                items.append(td)
        for url in self._images:
            items.append(ui.MediaGallery(discord.MediaGalleryItem(url)))
        if self._footer:
            items.append(ui.Separator())
            items.append(ui.TextDisplay(f"-# {self._footer}"))
        if not items:
            # Discord rejects an empty container; keep the message valid.
            items = [ui.TextDisplay("​")]
        # accent_colour stays None on purpose: no coloured bar on the left.
        return ui.Container(*items[:MAX_COMPONENTS], spoiler=self._spoiler)

    def view(self, *rows, timeout=180.0) -> ui.LayoutView:
        """Return a LayoutView holding this panel, plus optional ActionRows/items."""
        v = ui.LayoutView(timeout=timeout)
        v.add_item(self.container())
        for r in rows:
            if r is not None:
                v.add_item(r)
        return v


def panel_view(title=None, description=None, *, rows=(), timeout=180.0, **kw) -> ui.LayoutView:
    """One-shot helper for the simplest case (title + description + buttons)."""
    p = Panel(title, description, **kw)
    return p.view(*rows, timeout=timeout)


def row(*items) -> ui.ActionRow:
    """Build an ActionRow from buttons/selects."""
    ar = ui.ActionRow()
    for it in items:
        if it is not None:
            ar.add_item(it)
    return ar
