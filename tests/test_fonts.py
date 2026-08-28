"""The typeface is ours, is pinned, and actually reaches the page.

Almost everything that can go wrong here looks exactly like success. A `@font-face` whose
`src` 404s renders the fallback stack; a break anywhere in the three-link chain from
Preflight to `--font-sans` renders the fallback stack; a file swapped for a different one
renders something perfectly legible. All three are reasonable-looking pages, and the only
failure a reader would notice unaided is the one nobody wants — a request leaving the
machine for a font host, visible only to whoever is watching the network.

So the assertions are on the compiled stylesheet, on the bytes on disk and on the bytes the
server hands out, rather than on the source that was supposed to produce them.
`tests/e2e/test_shell.py` reads the face off a rendered page, which is the only check here
that a browser can make and a file cannot.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from aer.web.templating import STATIC_DIR, TEMPLATES_DIR

FONTS = STATIC_DIR / "fonts"
COMPILED = STATIC_DIR / "css" / "app.css"

# Three families, eight files, all SIL Open Font License 1.1:
#
#   `@fontsource/barlow-semi-condensed@5.3.0`   — object identity, 600 and 700
#   `@fontsource-variable/source-sans-3@5.3.0`  — interface and reading, 200-900 variable
#   `@ibm/plex-mono-variable@1.0.0`             — figures and records, 100-700 variable
#
# Pinned rather than described. ADR 0006 says a vendored asset's version and SHA-256 are
# recorded in the commit that adds it, and a commit message is a record nobody diffs: a
# font file swapped for another is a binary change that reviews as "+1 -1 binary file".
# Here it is a red build.
PINNED = {
    "barlow-semi-condensed-latin-600-normal.woff2": (
        "f158417e9207b5362f9b71a2fe779ce5bb836ad972f38445c3163af39d2c998d"
    ),
    "barlow-semi-condensed-latin-700-normal.woff2": (
        "fb958c8c20a05552ac8a85d925d96028d52565792650e941a5fe96b6997aa5cb"
    ),
    "barlow-semi-condensed-latin-ext-600-normal.woff2": (
        "5a58c6c9887c7306399176e96bdeb0c2c3b2935a43f89e25ac4e342b03631843"
    ),
    "barlow-semi-condensed-latin-ext-700-normal.woff2": (
        "109b73c772cc798422a6879f32c96ad646cb65f5c211933cb05f60c4fc2ab540"
    ),
    "ibm-plex-mono-latin-ext-wght-normal.woff2": (
        "a00cbadcf0f6cb76ef82593ac4b7c8810a8aea61b8bf5744aa8e9b6fc7e0ea03"
    ),
    "ibm-plex-mono-latin-wght-normal.woff2": (
        "632cb6cee5e90d89bd4354ff362bcffcef1384c19b603ddd2830561716e1f440"
    ),
    "source-sans-3-latin-ext-wght-normal.woff2": (
        "a85a7459bdb3cdc1136751e151a506bae653fc29ada3ca86237477df6f1b59e6"
    ),
    "source-sans-3-latin-wght-normal.woff2": (
        "7a19a7027e125257d310c6dbd78ae3a30b5ea1e3794d60b12bb28227a003bfda"
    ),
}

# What each family is for, and the variable axis it declares. A static family declares one
# weight per file; a variable one declares a range, and the range is the assertion that
# matters — see `test_no_weight_the_type_scale_uses_is_synthesised`.
FAMILIES = {
    "Barlow Semi Condensed": ("barlow-semi-condensed", None),
    "Source Sans 3 Variable": ("source-sans-3", "200 900"),
    "IBM Plex Mono Var": ("ibm-plex-mono", "100 700"),
}

# The weights the design system's type scale asks for, per family. 450 and 550 are the
# reason the mono is IBM's variable release rather than fontsource's static hundreds.
SCALE_WEIGHTS = {
    "Barlow Semi Condensed": (600, 700),
    "Source Sans 3 Variable": (400, 500, 600),
    "IBM Plex Mono Var": (450, 500, 550, 600),
}

LICENCES = (
    "LICENSE-Barlow-Semi-Condensed.txt",
    "LICENSE-Source-Sans-3.txt",
    "LICENSE-IBM-Plex-Mono.txt",
)


def _declares(block: str, family: str) -> bool:
    """Whether an `@font-face` block is for this family.

    The quotes are optional: Lightning CSS drops them from `font-family:"Barlow Semi
    Condensed"` when it minifies, so a matcher that requires them silently matches nothing
    and every assertion built on it passes over an empty list.
    """
    declared = re.search(r"font-family:\s*\"?([^;\"]+)\"?", block)
    return declared is not None and declared.group(1).strip() == family


class TestTheFontIsVendored:
    @pytest.mark.parametrize("name", sorted(PINNED))
    def test_the_file_is_committed_and_is_what_it_says(self, name: str) -> None:
        path = FONTS / name
        assert path.is_file(), f"{name} is missing from static/fonts"

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == PINNED[name], (
            f"{name} is not the file this repository vendored. Either it was replaced — in "
            "which case say so, with its provenance — or something went wrong."
        )

    @pytest.mark.parametrize("name", LICENCES)
    def test_the_licence_travels_with_it(self, name: str) -> None:
        # SIL OFL 1.1 requires the notice to accompany the font. A vendored typeface with
        # no licence beside it is a licensing problem shipped in a static directory, and
        # three families is three notices — the one nobody copies is the one that matters.
        licence = (FONTS / name).read_text(encoding="utf-8")

        assert "SIL Open Font License" in licence

    def test_nothing_is_shipped_that_no_face_declares(self) -> None:
        """The complement of the pin: a file left behind after a family is replaced.

        Retiring Inter meant deleting two woff2 and a notice, and a leftover binary in the
        served tree is a file the operator downloads on the one page that happens to name it
        — or, more likely, dead weight nobody removes because nothing points at it.
        """
        shipped = {path.name for path in FONTS.glob("*.woff2")}

        assert shipped == set(PINNED), (
            f"unpinned font files in the static tree: {shipped - set(PINNED)}"
        )

    def test_every_notice_belongs_to_a_family_that_is_here(self) -> None:
        stale = {path.name for path in FONTS.glob("LICENSE-*")} - set(LICENCES)

        assert not stale, f"licences for families that are no longer shipped: {sorted(stale)}"

    def test_no_italic_face_is_shipped(self) -> None:
        # Nothing in the templates is italic, and three italic families would be another
        # 150 kB committed for the look of completeness. The design system is explicit that
        # italic is never synthesised, which is a rule about faces we *do* ship: if an italic
        # ever appears in a template, this is the test that says the font has to come with it.
        shipped = {path.name for path in FONTS.glob("*.woff2")}

        assert not any("italic" in name for name in shipped), sorted(shipped)


class TestNothingIsFetchedFromAnybodyElse:
    def test_every_font_url_is_relative(self) -> None:
        """ADR 0006's rule, read off the compiled stylesheet rather than the source.

        A `url()` naming a host would send a request from a page that can reach the
        database, the artefact store and the provider credentials — and would break the
        page entirely on a machine with no internet connection, which is the state this
        application is designed for.
        """
        urls = re.findall(r"url\(([^)]*)\)", COMPILED.read_text(encoding="utf-8"))

        absolute = [url for url in urls if re.match(r"^\s*[\"']?(https?:)?//", url)]
        assert not absolute, f"the stylesheet fetches from elsewhere: {absolute}"

    def test_both_faces_point_at_the_files_that_exist(self) -> None:
        # A `@font-face` whose `src` 404s renders the fallback stack, which is a page that
        # looks fine. Nothing about it says the typeface never arrived.
        urls = re.findall(r"url\(([^)]*woff2)\)", COMPILED.read_text(encoding="utf-8"))

        assert len(urls) == len(PINNED)
        for url in urls:
            name = url.strip("\"'").rsplit("/", 1)[-1]
            assert (FONTS / name).is_file(), f"the stylesheet asks for {name}, which is not there"

    def test_the_page_preloads_the_common_face_only(self) -> None:
        body = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")

        assert 'as="font"' in body
        # `crossorigin` even same-origin: fonts are fetched in CORS mode, and a preload
        # without it is a second request rather than a head start.
        assert "crossorigin" in body
        assert "inter-latin-ext" not in body, (
            "latin-ext is fetched by unicode-range on the pages that need it; preloading "
            "it spends 85 kB on every page for characters most of them do not have."
        )


class TestTheTypefacesReachThePage:
    def test_the_chain_from_preflight_to_source_sans_holds(self) -> None:
        """Three links, one of them inside a dependency.

        Preflight sets `font-family` on `html` from `--default-font-family`; Tailwind's own
        theme defines that as `--theme(--font-sans, initial)`; this repository sets
        `--font-sans`. Overriding the last is enough *while the chain holds*, and a break
        anywhere in it — a renamed variable in a Tailwind upgrade, most likely — leaves
        every page in the stock system stack, which is indistinguishable from a font that
        loaded. So it is followed here rather than assumed.
        """
        compiled = COMPILED.read_text(encoding="utf-8")

        preflight = re.search(r"html,\s*:host\{([^}]*)\}", compiled)
        assert preflight is not None, "Preflight no longer sets a font on html"
        assert "font-family:var(--default-font-family" in preflight.group(1)
        assert "--default-font-family:var(--font-sans)" in compiled
        assert re.search(r'--font-sans:\s*"Source Sans 3 Variable"', compiled)

    @pytest.mark.parametrize(
        ("token", "family"),
        [
            ("--font-sans", "Source Sans 3 Variable"),
            ("--font-display", "Barlow Semi Condensed"),
            ("--font-data", "IBM Plex Mono Var"),
            ("--font-mono", "IBM Plex Mono Var"),
        ],
    )
    def test_each_role_names_its_family_first(self, token: str, family: str) -> None:
        """Three roles, three faces, and the token is the only way a template picks one.

        `--font-mono` is here because templates written before the token existed use
        Tailwind's `font-mono`; leaving it on the stock stack would set half the hashes on a
        page in the vendored face and half in whatever the machine supplies.
        """
        compiled = COMPILED.read_text(encoding="utf-8")
        stack = re.search(rf"{token}:([^;]*);", compiled)

        assert stack is not None, f"{token} is not in the compiled stylesheet"
        assert stack.group(1).strip().lstrip('"').startswith(family), stack.group(1)

    @pytest.mark.parametrize(
        "token", ["--font-sans", "--font-display", "--font-data", "--font-mono"]
    )
    def test_every_stack_still_has_somewhere_to_fall_back_to(self, token: str) -> None:
        # What renders while the woff2 is in flight, and for ever if it 404s. A single-name
        # stack turns a missing font into whatever the browser feels like.
        compiled = COMPILED.read_text(encoding="utf-8")
        stack = re.search(rf"{token}:([^;]*);", compiled)

        assert stack is not None
        assert re.search(r"(sans-serif|monospace)\s*$", stack.group(1).strip())

    @pytest.mark.parametrize("family", sorted(SCALE_WEIGHTS))
    def test_no_weight_the_type_scale_uses_is_synthesised(self, family: str) -> None:
        """The assertion the whole vendoring decision turns on.

        A weight no face declares is not a missing weight — it is a *fake* one: the browser
        smears the nearest cut and renders a heavier, blurrier approximation that looks
        deliberate. The type scale asks for IBM Plex Mono at 450 and 550, which no static cut
        has, and that is why the mono comes from IBM's variable release rather than
        fontsource's hundreds. If somebody swaps it back for the static family, this fails
        rather than the page quietly going soft.
        """
        compiled = COMPILED.read_text(encoding="utf-8")
        declared: set[int] = set()
        for block in re.findall(r"@font-face\{([^}]*)\}", compiled):
            if not _declares(block, family):
                continue
            weights = re.search(r"font-weight:(\d+)(?:\s+(\d+))?", block)
            assert weights is not None, f"a {family} face declares no weight: {block}"
            low = int(weights.group(1))
            high = int(weights.group(2)) if weights.group(2) else low
            declared.update(range(low, high + 1))

        assert declared, f"no @font-face declares {family}"
        missing = sorted(w for w in SCALE_WEIGHTS[family] if w not in declared)
        assert not missing, (
            f"{family} is asked for at {missing} and no vendored face declares those weights. "
            "The browser will synthesise them, which is a heavier, blurrier fake of a weight "
            "the design chose deliberately."
        )

    def test_each_family_covers_both_subsets(self) -> None:
        """A family with no latin-ext cut sets Škoda's diacritic in the fallback, mid-word.

        Issuer names come out of filings and an LSE listing is routinely a European domicile,
        so this is the ordinary case rather than the exotic one.
        """
        compiled = COMPILED.read_text(encoding="utf-8")
        for family, (stem, _axis) in FAMILIES.items():
            for subset in ("latin", "latin-ext"):
                wanted = f"{stem}-{subset}-"
                faces = [
                    block
                    for block in re.findall(r"@font-face\{([^}]*)\}", compiled)
                    if _declares(block, family) and wanted in block
                ]
                assert faces, f"{family} has no {subset} face"
