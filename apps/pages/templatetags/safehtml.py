"""Containment filter for admin-authored HTML fragments (e.g. FAQ answers).

Each FAQ answer is admin-entered raw HTML rendered on a shared page. A single
malformed fragment — an unclosed tag or (as happened once) a missing closing
quote on an ``href`` — would otherwise "bleed" its styling into every sibling
item below it. This filter re-serialises each fragment through the stdlib HTML
parser so that:

  * unknown/dangerous tags (``<script>`` …) are dropped, their text kept;
  * only a small allow-list of formatting tags/attributes survives;
  * ``javascript:``-style URLs are stripped;
  * any tags left open at the end are force-closed.

The net effect: a broken fragment can degrade *itself* but can never escape its
own boundary. Zero third-party dependencies.
"""

from html import escape
from html.parser import HTMLParser

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# Tag -> set of attributes that are allowed to survive.
ALLOWED = {
    "a": {"href", "title", "target", "rel"},
    "p": set(),
    "br": set(),
    "ul": set(),
    "ol": set(),
    "li": set(),
    "strong": set(),
    "b": set(),
    "em": set(),
    "i": set(),
    "u": set(),
    "span": set(),
    "h2": set(),
    "h3": set(),
    "h4": set(),
}
VOID = {"br"}


def _safe_url(url):
    return not url.strip().lower().startswith(("javascript:", "data:", "vbscript:"))


class _Normalizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            return  # drop the tag, keep any text via handle_data
        parts = [tag]
        for key, val in attrs:
            if key in ALLOWED[tag] and val is not None:
                if key == "href" and not _safe_url(val):
                    continue
                parts.append('%s="%s"' % (key, escape(val, quote=True)))
        self.out.append("<%s>" % " ".join(parts))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1] == tag:
            self.stack.pop()
            self.out.append("</%s>" % tag)

    def handle_endtag(self, tag):
        if tag not in ALLOWED or tag in VOID:
            return
        if tag in self.stack:
            # Close everything down to (and including) the matching tag.
            while self.stack:
                open_tag = self.stack.pop()
                self.out.append("</%s>" % open_tag)
                if open_tag == tag:
                    break

    def handle_data(self, data):
        self.out.append(escape(data, quote=False))

    def result(self):
        self.close()
        while self.stack:
            self.out.append("</%s>" % self.stack.pop())
        return "".join(self.out)


@register.filter(name="safe_fragment")
def safe_fragment(value):
    """Render an admin HTML fragment as safe, self-contained markup."""
    if not value:
        return ""
    parser = _Normalizer()
    parser.feed(str(value))
    return mark_safe(parser.result())
