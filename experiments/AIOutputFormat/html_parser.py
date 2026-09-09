#!/usr/bin/env python3
"""HTML parsing: build a minimal DOM tree from HTML-formatted model output and
extract items via a sequence of strategies (formatting-tag-only, <li>, bare
list text, fallback tags, invalid-tag extraction, plain text). Fully
self-contained -- no other parser shares this logic."""

import re
from html.parser import HTMLParser

# ── HTML Tree-Building Helpers ────────────────────────────────────────────────

# Maps lowercase HTML tag names to their display form used in cleanup key names.
_HTML_TAG_DISPLAY = {
    'html': 'HTML', 'body': 'Body', 'head': 'Head',
    'ul': 'UL', 'ol': 'OL', 'li': 'LI',
    'p': 'P', 'div': 'Div', 'span': 'Span',
    'article': 'Article', 'section': 'Section', 'br': 'BR',
    'b': 'Bold', 'i': 'Italic', 'strong': 'Strong', 'em': 'Emphasis', 'u': 'Underline',
    'pre': 'Pre',
}

def _tag_cleanup_name(tag):
    """Return the cleanup key name for a given HTML tag, e.g. 'li' -> 'Extract-From-LI-Tags'."""
    display = _HTML_TAG_DISPLAY.get(tag.lower(), tag.capitalize())
    return f"Extract-From-{display}-Tags"


class _HtmlNode:
    """Minimal DOM tree node for HTML parsing."""
    __slots__ = ('tag', 'children', 'parent')

    def __init__(self, tag):
        self.tag = tag          # string tag name, or None for virtual root / text nodes
        self.children = []      # list of _HtmlNode or str
        self.parent = None

    def append(self, child):
        """Append a child node or text string, setting parent if it's a node."""
        self.children.append(child)
        if isinstance(child, _HtmlNode):
            child.parent = self

    def iter_tag(self, tag):
        """Depth-first generator of descendant nodes with the given tag name."""
        for child in self.children:
            if isinstance(child, _HtmlNode):
                if child.tag == tag:
                    yield child
                yield from child.iter_tag(tag)

    def ancestor_tags(self):
        """Return list of ancestor tag names from outermost to parent (excludes virtual root)."""
        tags = []
        node = self.parent
        while node is not None and node.tag is not None:
            tags.append(node.tag)
            node = node.parent
        tags.reverse()
        return tags

    def text_content(self, br_as_newline=False):
        """Recursively collect text, optionally replacing <br> children with newline.
        Returns (text, had_br) where had_br is True if any <br> was encountered."""
        parts = []
        had_br = False
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif isinstance(child, _HtmlNode):
                if child.tag == 'br':
                    had_br = True
                    if br_as_newline:
                        parts.append('\n')
                else:
                    sub_text, sub_br = child.text_content(br_as_newline)
                    parts.append(sub_text)
                    if sub_br:
                        had_br = True
        return ''.join(parts), had_br


class _HtmlTreeBuilder(HTMLParser):
    """Builds an _HtmlNode tree from HTML content."""

    _VOID_TAGS = frozenset({
        'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
        'link', 'meta', 'param', 'source', 'track', 'wbr',
    })

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode(None)   # virtual root
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _HtmlNode(tag)
        self.stack[-1].append(node)
        if tag not in self._VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        # Pop back to the matching open tag (handles malformed/unclosed HTML)
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                return

    def handle_data(self, data):
        self.stack[-1].append(data)


def _parse_html_tree(content):
    """Parse HTML content into an _HtmlNode tree. Returns virtual root node.
    Swallows all exceptions so callers never crash on malformed HTML."""
    builder = _HtmlTreeBuilder()
    try:
        builder.feed(content)
    except Exception:
        pass
    return builder.root


def _find_leaf_tag_nodes(root, tag):
    """Return all nodes with the given tag that have no descendant of the same tag.
    This prevents double-counting from nested same-tag containers (e.g. <div><div>)."""
    results = []
    for node in root.iter_tag(tag):
        # Check if this node contains any descendant with the same tag
        has_nested = any(True for _ in node.iter_tag(tag))
        if not has_nested:
            results.append(node)
    return results


def _items_from_nodes(nodes):
    """Extract text items from a list of _HtmlNode objects.
    For each node: if the text content (after br-splitting) contains newlines,
    splits into multiple items. Otherwise yields a single item.
    Returns (items, had_br, was_line_split)."""
    items = []
    had_br = False
    was_line_split = False
    for node in nodes:
        text, node_had_br = node.text_content(br_as_newline=True)
        if node_had_br:
            had_br = True
        stripped = text.strip()
        if '\n' in stripped:
            # Split by newlines and treat each non-empty line as an item
            lines = [ln.strip() for ln in stripped.split('\n') if ln.strip()]
            items.extend(lines)
            was_line_split = True
        elif stripped:
            items.append(stripped)
    return items, had_br, was_line_split


def _path_tag_cleanups(item_nodes, item_tag):
    """Return an ordered list of Extract-From-X-Tags cleanup keys representing the
    DOM path from root to item_tag.  Uses item_nodes[0]'s ancestor chain as the
    representative path.  Deduplicates while preserving outermost-to-innermost order,
    then appends item_tag at the end."""
    if not item_nodes:
        return [_tag_cleanup_name(item_tag)]
    ancestor_tags = item_nodes[0].ancestor_tags()
    # Deduplicate while preserving order
    seen = set()
    ordered = []
    for t in ancestor_tags + [item_tag]:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return [_tag_cleanup_name(t) for t in ordered]


def _find_nodes_with_unknown_tags(root, known_tags):
    """Depth-first search for nodes whose tag name is not in known_tags."""
    result = []
    for child in root.children:
        if isinstance(child, _HtmlNode) and child.tag is not None:
            if child.tag not in known_tags:
                result.append(child)
            result.extend(_find_nodes_with_unknown_tags(child, known_tags))
    return result


# Tags tried as fallbacks when no <li> tags are found, in priority order.
_HTML_FALLBACK_TAGS = ['p', 'span', 'div', 'article', 'section']

# Structural tags that don't indicate formatted content (allowed in any HTML)
_HTML_STRUCTURAL_TAGS = frozenset(['html', 'body', 'head', 'br', 'hr'])

# Tags that indicate formatted content or containers
_HTML_NON_STRUCTURAL_TAGS = frozenset([
    'li', 'p', 'span', 'div', 'article', 'section', 'ol', 'ul',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'b', 'i', 'strong', 'em', 'u', 's', 'code', 'pre',
])


def _all_tags_in_tree(root):
    """Return set of all tag names in the tree (excluding virtual root and text nodes)."""
    tags = set()
    for child in root.children:
        if isinstance(child, _HtmlNode) and child.tag is not None:
            tags.add(child.tag)
            tags.update(_all_tags_in_tree(child))
    return tags


def _only_has_tag(root, target_tag):
    """Return True if the tree contains target_tag and no other non-structural tags.
    Only structural tags (html, body, head, br, hr) are allowed besides the target."""
    all_tags = _all_tags_in_tree(root)
    target_nodes = list(root.iter_tag(target_tag))
    if not target_nodes:
        return False
    # Check if any non-structural tags exist other than the target
    other_tags = (all_tags & _HTML_NON_STRUCTURAL_TAGS) - {target_tag}
    return len(other_tags) == 0


def _detect_br_tags(tag_nodes):
    """Check if any BR tags are present in the tag nodes.
    Returns cleanup list: ["Remove-BR-Tags"] if BR tags found, else []."""
    for node in tag_nodes:
        _, had_br = node.text_content(br_as_newline=True)
        if had_br:
            return ["Remove-BR-Tags"]
    return []


def _extract_from_html_formatting_tag(tag_nodes, tag):
    """Extract items that are wrapped in the specified formatting tag.
    Returns (items, cleanups) where cleanups refers to the extraction cleanup task only."""
    if not tag_nodes:
        return [], []

    items, _, _ = _items_from_nodes(tag_nodes)
    cleanups = [_tag_cleanup_name(tag)]
    return items, cleanups


def _try_numbered_items_in_tag(raw_items, tag_nodes, tag, had_br, cleanups, quality_issues):
    """If every item in raw_items is a numbered entry (e.g. "<span>1. Lion</span>"),
    strip the numbers and return the items. Returns None if not all are numbered."""
    num_matches = [re.match(r'^\d+\.\s+(.+)$', it) for it in raw_items]
    if not (raw_items and all(num_matches)):
        return None
    items = [m.group(1) for m in num_matches]
    cleanups.extend(_path_tag_cleanups(tag_nodes, tag))
    if had_br:
        cleanups.append("Remove-BR-Tags")
    cleanups.append("HTML-Numbered-Items-In-Tags")
    quality_issues.append("numbered-items-in-tags")
    return items


def _split_comma_separated_items(raw_items, tag):
    """Split inline comma-separated values within items of p/span/div tags.
    Returns (final_items, comma_split_used)."""
    final_items = []
    comma_split_used = False
    for item in raw_items:
        if ',' in item and tag in ['p', 'span', 'div']:
            parts = [p.strip() for p in item.split(',') if p.strip()]
            if len(parts) > 1 and all(len(p) < 100 for p in parts):
                final_items.extend(parts)
                comma_split_used = True
                continue
        final_items.append(item)
    return final_items, comma_split_used


def _try_fallback_tags(root, cleanups, quality_issues):
    """Try each tag in _HTML_FALLBACK_TAGS priority order; return raw items for the
    first tag that yields any (mutating cleanups/quality_issues along the way), or
    [] if none do. Extracted from parse_html's fallback-tag loop."""
    for tag in _HTML_FALLBACK_TAGS:
        tag_nodes = _find_leaf_tag_nodes(root, tag)
        if not tag_nodes:
            continue
        raw_items, had_br, was_line_split = _items_from_nodes(tag_nodes)
        if not raw_items:
            continue

        numbered_items = _try_numbered_items_in_tag(raw_items, tag_nodes, tag, had_br, cleanups, quality_issues)
        if numbered_items is not None:
            return numbered_items

        final_items, comma_split_used = _split_comma_separated_items(raw_items, tag)
        cleanups.extend(_path_tag_cleanups(tag_nodes, tag))
        if had_br:
            cleanups.append("Remove-BR-Tags")
        if comma_split_used:
            quality_issues.append("comma-separated")
        if not was_line_split:
            quality_issues.append("single-span-tag")
        return final_items

    return []


def _try_html_codefence_fallback(content, cleanups, quality_issues):
    """If content is a fenced code block, extract numbered items from inside it.
    If it's fenced but not all-numbered, drop the fence and return the inner content
    to continue parsing as HTML. Returns (items_or_None, content_to_use_next)."""
    if not content.strip().startswith('```'):
        return None, content

    code_block_pattern = r'^```[\w]*\n(.*?)\n```\s*$'
    match = re.search(code_block_pattern, content.strip(), re.DOTALL)
    if not match:
        return None, content

    extracted_content = match.group(1)
    cleanups.append("Extract-from-Codefence-Markdown")
    quality_issues.append("html_no_markup")

    # If inner content is an all-numbered list, return it immediately
    numbered_pattern = r'^\d+\.\s+(.+)$'
    numbered_items = [
        re.match(numbered_pattern, line.strip()).group(1)
        for line in extracted_content.split('\n')
        if re.match(numbered_pattern, line.strip())
    ]
    if numbered_items:
        return numbered_items, content

    # Otherwise fall through with extracted content (drop the fence)
    return None, extracted_content


def _try_formatting_tag_only(root, cleanups, quality_issues):
    """Quality-issue path: if the document contains only one specific formatting
    tag (b/i/em/u/pre), extract items from it. Returns items if successful, else None."""
    for tag, quality_issue in [('b', 'HTML_Only_Bold_Tags'), ('i', 'HTML_Only_Italic_Tags'), ('em', 'HTML_Only_Emphasis_Tags'), ('u', 'HTML_Only_Underline_Tags'), ('pre', 'HTML_Only_Pre_Tags')]:
        if _only_has_tag(root, tag):
            tag_nodes = _find_leaf_tag_nodes(root, tag)
            items, extraction_cleanups = _extract_from_html_formatting_tag(tag_nodes, tag)
            cleanups.extend(extraction_cleanups)
            cleanups.extend(_detect_br_tags(tag_nodes))
            quality_issues.append(quality_issue)
            if items:
                return items
    return None


def _try_li_primary_path(root, cleanups):
    """Primary path: extract items from <li> tags. Returns items (even if empty)
    once any <li> is found, or None if there are no <li> tags at all."""
    li_nodes = _find_leaf_tag_nodes(root, 'li')
    if not li_nodes:
        return None
    items, had_br, _ = _items_from_nodes(li_nodes)
    cleanups.extend(_path_tag_cleanups(li_nodes, 'li'))
    if had_br:
        cleanups.append("Remove-BR-Tags")
    return items


def _try_bare_list_path(root, cleanups, quality_issues):
    """UL/OL with unwrapped text: items directly in the list tag, with no <li>
    children. Returns items if found, else None."""
    for list_tag in ['ul', 'ol']:
        list_nodes = list(root.iter_tag(list_tag))
        if not list_nodes:
            continue
        # Only process nodes that have bare text children but no <li> children
        bare_nodes = [
            n for n in list_nodes
            if any(isinstance(c, str) and c.strip() for c in n.children)
            and not any(isinstance(c, _HtmlNode) and c.tag == 'li' for c in n.children)
        ]
        if not bare_nodes:
            continue
        raw_items, had_br, _ = _items_from_nodes(bare_nodes)
        if raw_items:
            cleanups.extend(_path_tag_cleanups(bare_nodes, list_tag))
            if had_br:
                cleanups.append("Remove-BR-Tags")
            quality_issues.append("items-not-in-li")
            return raw_items
    return None


def _try_invalid_tag_fallback(root, cleanups, quality_issues):
    """Extract text content from unrecognized/invalid tags, or the tag name
    itself for hollow tags (e.g. <tiger>). Returns items (possibly empty)."""
    known_tags = set(_HTML_TAG_DISPLAY.keys()) | _HtmlTreeBuilder._VOID_TAGS
    invalid_nodes = _find_nodes_with_unknown_tags(root, known_tags)
    if not invalid_nodes:
        return []

    items = []
    has_hollow_tags = False
    for node in invalid_nodes:
        text, _ = node.text_content()
        text = text.strip()
        if text:
            items.append(text)
        elif node.tag:
            # Hollow tag: tag name IS the content (e.g. <tiger>)
            items.append(node.tag)
            has_hollow_tags = True
    if items:
        cleanups.append("Extract-From-Invalid-HTML-Tags")
        quality_issues.append("invalid-html-tags")
        if has_hollow_tags:
            quality_issues.append("pointy-bracket-wrapping")
    return items


def _try_plain_text_fallback(content, cleanups, quality_issues):
    """No HTML structure detected: treat each non-blank line as an item, stripping
    any stray tags and detecting numbered lists. Returns items (possibly empty)."""
    plain_items = [line.rstrip('\n\r') for line in content.split('\n') if line.strip()]
    if not plain_items:
        return []

    # Strip HTML tags from each line before classification. Files that mix an
    # HTML-tagged title line (e.g. <u>Animal Names</u>) with plain numbered items
    # need the tags removed first so the numbered-list check works correctly.
    clean_lines = [re.sub(r'<[^>]+>', '', line).strip() for line in plain_items]
    clean_lines = [l for l in clean_lines if l]

    numbered_pattern = r'^\d+\.\s+(.+)$'
    num_matches = [re.match(numbered_pattern, l) for l in clean_lines]
    numbered_items = [m.group(1) for m in num_matches if m]

    if numbered_items and all(num_matches):
        # Every line is a numbered item — clean numbered list in plain text.
        cleanups.append("HTML-Numbered-List-Stripping")
        quality_issues.append("html_no_markup")
        return numbered_items

    if numbered_items:
        # Mix of numbered items and non-numbered lines (e.g. a title).
        # If the non-numbered lines are all short (≤5 words), treat them as
        # header/title noise and extract only the numbered items.
        non_numbered = [clean_lines[i] for i, m in enumerate(num_matches) if not m]
        if all(len(l.split()) <= 5 for l in non_numbered):
            cleanups.append("HTML-Numbered-List-Stripping")
            quality_issues.append("html_no_markup")
            return numbered_items
        cleanups.append("HTML-PlainText-Fallback")
        quality_issues.append("html_no_markup")
        return clean_lines

    cleanups.append("HTML-PlainText-Fallback")
    quality_issues.append("html_no_markup")
    return clean_lines


def parse_html(content):
    """Parse HTML using a DOM tree. Tries each strategy in turn: codefence fallback,
    formatting-tag-only quality path, <li> primary path, bare UL/OL text, fallback
    tags in priority order (<p>/<span>/<div>/<article>/<section>), invalid-tag
    extraction, and finally plain-text. Returns (items, cleanups, quality_issues)."""
    cleanups = []
    quality_issues = []

    # Try codefence extraction first (takes content, may modify it)
    codefence_items, content = _try_html_codefence_fallback(content, cleanups, quality_issues)
    if codefence_items is not None:
        return codefence_items, cleanups, quality_issues

    root = _parse_html_tree(content)

    # Define strategies that operate on parsed root. Each returns items (may be None or empty).
    # Try each in order; first one that returns non-empty items succeeds.
    strategies = [
        _try_formatting_tag_only,
        _try_li_primary_path,
        _try_bare_list_path,
        _try_fallback_tags,
        _try_invalid_tag_fallback,
    ]

    for strategy in strategies:
        # Most strategies take (root, cleanups, quality_issues); _try_li_primary_path only takes (root, cleanups)
        if strategy == _try_li_primary_path:
            items = strategy(root, cleanups)
        else:
            items = strategy(root, cleanups, quality_issues)
        if items:
            return items, cleanups, quality_issues

    # Final fallback: try as plain text
    items = _try_plain_text_fallback(content, cleanups, quality_issues)
    return items, cleanups, quality_issues
