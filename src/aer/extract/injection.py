"""Noticing when a document is trying to give instructions.

Threat T2. Two scans, and the split is not arbitrary — it follows what can be seen from where.

:func:`scan_text` reads the extracted text and needs nothing else. Pure, safe to call anywhere,
and it works for every extractor: the PDF one in task 14 inherits it without a line of new code.

:func:`scan_markup` needs the parse tree, because "this text was invisible" is a fact about the
markup that the text no longer carries. It therefore runs **inside the sandbox**, during the
parse that is already happening — re-parsing untrusted bytes outside that boundary to look for
attacks would be an odd way to defend against them.

**Nothing here blocks anything, and that is the design.** Every one of these signals has an
innocent explanation: a print stylesheet, an accessibility label, a base64 image, a Korean
document using zero-width joiners, a filing that genuinely discusses prompt injection. What
actually stops an injected instruction is structural — agents have no tool a document could
persuade them to misuse (T3), and fetched content reaches a prompt wrapped and labelled
(:mod:`aer.agents.untrusted`). Detection exists so a human at gate 2 knows where to look.

That framing decides the thresholds. A false positive costs a badge on a page and a minute of
somebody's attention. A false negative costs nothing either, because the containment does not
depend on detection — so these are tuned to be *informative* rather than either strict or
permissive.
"""

from __future__ import annotations

import re
from typing import Final

from selectolax.parser import HTMLParser, Node

from aer.core.schemas.extraction import Locator
from aer.core.schemas.injection import Finding, InjectionSignal

__all__ = ["scan_markup", "scan_text"]

# Phrases that read as an attempt to countermand earlier instructions. Deliberately specific:
# "ignore" on its own appears in every filing that discusses accounting policy.
_OVERRIDE_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}?"
            r"\b(?:previous|prior|above|earlier|preceding|all)\b[^.\n]{0,40}?"
            r"\b(?:instruction|prompt|rule|direction|command|guideline)s?\b",
            re.IGNORECASE,
        ),
        "asks for earlier instructions to be set aside",
    ),
    (
        re.compile(
            r"\byou are now\b|\bnew instructions?\s*:|\byour new (?:task|role|goal)\b", re.I
        ),
        "attempts to reassign the reader's role or task",
    ),
    (
        re.compile(r"\b(?:system|developer)\s+(?:prompt|message|instruction)s?\b", re.IGNORECASE),
        "refers to the system prompt, which a document has no business knowing about",
    ),
    (
        re.compile(
            r"\bdo not (?:cite|verify|check)\b|\bno citations? (?:are )?(?:needed|required)\b", re.I
        ),
        "asks for the evidence rules to be relaxed",
    ),
    (
        re.compile(
            r"\b(?:rate|mark|conclude|report)\s+(?:this|it)\s+(?:a\s+)?(?:buy|sell|hold)\b", re.I
        ),
        "asks for a specific rating, which no document may set",
    ),
)

# Chat-format markers. A document containing these is impersonating the frame it is quoted
# inside, which has no innocent reading in a filing.
_ROLE_MARKERS: Final[re.Pattern[str]] = re.compile(
    r"<\|im_(?:start|end)\|>|\[/?INST\]|<\|(?:system|user|assistant)\|>|"
    r"^\s{0,4}(?:###\s*)?(?:System|Assistant|Human)\s*:",
    re.IGNORECASE | re.MULTILINE,
)

# Attempts to have something sent somewhere. Closed structurally — there is no agent-callable
# tool that takes a URL — so this is purely a signal that somebody tried.
_EXFILTRATION: Final[re.Pattern[str]] = re.compile(
    r"\b(?:send|post|upload|transmit|exfiltrate|leak)\b[^.\n]{0,60}?https?://|"
    r"\bcurl\s+-|\bfetch\s*\(\s*[\"']https?://",
    re.IGNORECASE,
)

# No visual width. Legitimate in Arabic, Hebrew and some Indic scripts; also the classic way to
# smuggle text past a human reader.
_ZERO_WIDTH: Final[re.Pattern[str]] = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\ufeff]+")

# A long unbroken run of base64. The threshold is high because filings are full of long tokens —
# accession numbers, CIKs, hex digests — and none of them run this far.
_ENCODED_BLOB_MINIMUM: Final = 200
_ENCODED_BLOB: Final[re.Pattern[str]] = re.compile(
    rf"[A-Za-z0-9+/]{{{_ENCODED_BLOB_MINIMUM},}}={{0,2}}"
)

# Inline styles that hide content. Whitespace-insensitive because minifiers are.
_HIDDEN_STYLES: Final[tuple[tuple[re.Pattern[str], InjectionSignal, str], ...]] = (
    (
        re.compile(r"display\s*:\s*none", re.IGNORECASE),
        InjectionSignal.HIDDEN_TEXT,
        "display:none",
    ),
    (
        re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE),
        InjectionSignal.HIDDEN_TEXT,
        "visibility:hidden",
    ),
    (
        re.compile(r"font-size\s*:\s*0(?:\.0+)?(?:px|em|pt|%)?\b", re.IGNORECASE),
        InjectionSignal.HIDDEN_TEXT,
        "a zero font size",
    ),
    (
        re.compile(r"opacity\s*:\s*0(?:\.0+)?\s*[;\"']?", re.IGNORECASE),
        InjectionSignal.INVISIBLE_STYLING,
        "full transparency",
    ),
    (
        re.compile(r"color\s*:\s*(?:#f{3,6}\b|white|rgba?\(\s*255\s*,\s*255\s*,\s*255)", re.I),
        InjectionSignal.INVISIBLE_STYLING,
        "white text",
    ),
    (
        re.compile(r"text-indent\s*:\s*-\s*\d{3,}", re.IGNORECASE),
        InjectionSignal.OFFSCREEN_TEXT,
        "a large negative text indent",
    ),
    (
        re.compile(r"(?:left|top)\s*:\s*-\s*\d{3,}\s*(?:px|em|pt)", re.IGNORECASE),
        InjectionSignal.OFFSCREEN_TEXT,
        "positioning far outside the page",
    ),
)

# HTML comments. Never rendered, always parsed — which is the whole appeal.
_COMMENT: Final[re.Pattern[str]] = re.compile(r"<!--(.*?)-->", re.DOTALL)

# Below this, a hidden element is a spacer, an icon or an empty container rather than a message.
_WORTH_REPORTING = 24


def scan_text(text: str) -> tuple[Finding, ...]:
    """Signals visible in the extracted text alone.

    Every match carries a locator into ``text``, so a reviewer can be shown the passage rather
    than told a category.
    """
    findings: list[Finding] = []

    for pattern, detail in _OVERRIDE_PATTERNS:
        findings += _matches(text, pattern, InjectionSignal.INSTRUCTION_OVERRIDE, detail)

    findings += _matches(
        text,
        _ROLE_MARKERS,
        InjectionSignal.ROLE_MARKER,
        "contains a chat-format role marker, which a filing has no reason to",
    )
    findings += _matches(
        text,
        _EXFILTRATION,
        InjectionSignal.EXFILTRATION_LURE,
        "asks for something to be sent to a URL",
    )
    findings += _matches(
        text,
        _ZERO_WIDTH,
        InjectionSignal.ZERO_WIDTH_CHARACTERS,
        "contains characters with no visual width",
    )
    findings += _matches(
        text,
        _ENCODED_BLOB,
        InjectionSignal.ENCODED_BLOB,
        f"contains an unbroken run of at least {_ENCODED_BLOB_MINIMUM} base64 characters",
    )

    return tuple(findings)


def scan_markup(tree: HTMLParser, text: str) -> tuple[Finding, ...]:
    """Signals that only exist before the markup is thrown away.

    Args:
        tree: The parsed document, **after** script and style nodes are removed and before
            anything else is. Passed in rather than parsed here, because this runs inside the
            extraction sandbox during the parse that is already happening.
        text: The extracted text, so a finding can be located in it. A hidden element's words
            are in there — the extractor keeps hidden text deliberately — so the locator is
            usually recoverable.
    """
    findings: list[Finding] = []

    for node in tree.css("[style], [hidden]"):
        findings += _hidden_by_styling(node, text)

    findings += _instructive_comments(tree.html or "")

    return tuple(findings)


# -- Internals -------------------------------------------------------------------------------


def _matches(
    text: str, pattern: re.Pattern[str], signal: InjectionSignal, detail: str
) -> list[Finding]:
    """Every match, as a located finding.

    Bounded at :data:`_MATCHES_PER_SIGNAL`. A document repeating the same trick two thousand
    times would otherwise produce two thousand rows of JSONB saying the same thing, and the
    reviewer needs to know it happened, not how many times it happened — which the detail
    records anyway.
    """
    found: list[Finding] = []
    for match in pattern.finditer(text):
        if len(found) >= _MATCHES_PER_SIGNAL:
            found.append(
                Finding.of(signal, detail=f"{detail} (and further occurrences, not all listed)")
            )
            break
        found.append(
            Finding.of(
                signal,
                detail=detail,
                evidence=text[match.start() : match.start() + 300],
                locator=Locator(char_start=match.start(), char_end=match.end()),
            )
        )
    return found


_MATCHES_PER_SIGNAL: Final = 5


def _hidden_by_styling(node: Node, text: str) -> list[Finding]:
    """Whether this element hides text, and if so where that text ended up."""
    content = node.text(separator=" ", strip=True).strip()
    if len(content) < _WORTH_REPORTING:
        return []

    style = node.attributes.get("style") or ""
    findings: list[Finding] = []

    if "hidden" in node.attributes and node.attributes.get("hidden") != "false":
        findings.append(
            _hidden_finding(InjectionSignal.HIDDEN_TEXT, "the hidden attribute", content, text)
        )

    for pattern, signal, what in _HIDDEN_STYLES:
        if pattern.search(style):
            findings.append(_hidden_finding(signal, what, content, text))

    return findings


def _hidden_finding(signal: InjectionSignal, what: str, content: str, text: str) -> Finding:
    """A finding for hidden content, located in the extracted text where possible.

    The element's own text and the extracted text can differ in whitespace — the extractor
    joins nodes with newlines — so a locator is only recorded when the content appears
    verbatim. An approximate locator would point a reviewer at nearly the right passage, which
    is worse than pointing them at none.
    """
    found = text.find(content)
    locator = Locator(char_start=found, char_end=found + len(content)) if found >= 0 else None
    return Finding.of(
        signal,
        detail=f"{len(content)} characters were hidden by {what}",
        evidence=content,
        locator=locator,
    )


def _instructive_comments(markup: str) -> list[Finding]:
    """Comments that read as instructions.

    Only the ones that do. Every HTML document is full of comments — build stamps, conditional
    tags, a developer's note to themselves — and flagging all of them would bury the one that
    matters. No locator: a comment is never rendered, so it is not in the extracted text at all.
    """
    findings: list[Finding] = []
    for match in _COMMENT.finditer(markup):
        body = match.group(1)
        for pattern, detail in _OVERRIDE_PATTERNS:
            if pattern.search(body):
                findings.append(
                    Finding.of(
                        InjectionSignal.COMMENT_INSTRUCTION,
                        detail=f"an HTML comment {detail}",
                        evidence=body,
                    )
                )
                break
        if len(findings) >= _MATCHES_PER_SIGNAL:
            break
    return findings
