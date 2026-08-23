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

# `@fontsource-variable/inter@5.3.0`, upstream Inter v20, SIL Open Font License 1.1.
#
# Pinned rather than described. ADR 0006 says a vendored asset's version and SHA-256 are
# recorded in the commit that adds it, and a commit message is a record nobody diffs: a
# font file swapped for another is a binary change that reviews as "+1 -1 binary file".
# Here it is a red build.
PINNED = {
    "inter-latin-wght-normal.woff2": (
        "3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62"
    ),
    "inter-latin-ext-wght-normal.woff2": (
        "34b9c504cab7a73e37b746343a449132e56cf7b5481af2cb81dc74dcff25c956"
    ),
}


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

    def test_the_licence_travels_with_it(self) -> None:
        # SIL OFL 1.1 requires the notice to accompany the font. A vendored typeface with
        # no licence beside it is a licensing problem shipped in a static directory.
        licence = (FONTS / "LICENSE-Inter.txt").read_text(encoding="utf-8")

        assert "SIL Open Font License" in licence
        assert "Inter Project Authors" in licence

    def test_no_italic_face_is_shipped(self) -> None:
        # Nothing in the templates is italic, and a face nobody renders is 52 kB committed
        # for the look of completeness. If an italic ever appears in a template, this is
        # the test that says the font has to come with it.
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


class TestTheTypefaceReachesThePage:
    def test_the_chain_from_preflight_to_inter_holds(self) -> None:
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
        assert re.search(r"--font-sans:\s*\"Inter Variable\"", compiled)

    def test_the_stack_still_has_somewhere_to_fall_back_to(self) -> None:
        # What renders while the woff2 is in flight, and for ever if it 404s. A single-name
        # stack turns a missing font into whatever the browser feels like.
        compiled = COMPILED.read_text(encoding="utf-8")
        stack = re.search(r"--font-sans:([^;]*);", compiled)

        assert stack is not None
        assert "sans-serif" in stack.group(1)

    def test_the_variable_face_covers_every_weight_the_templates_use(self) -> None:
        """One file, weights 100-900, so four weights on a page is one request.

        The check that matters is the range: a `font-weight: 400` declaration would leave
        `font-semibold` and `font-medium` — which most of these templates use — synthesised
        by the browser, which is a heavier, blurrier fake of a weight the file contains.
        """
        compiled = COMPILED.read_text(encoding="utf-8")

        assert compiled.count("font-weight:100 900") == len(PINNED)
