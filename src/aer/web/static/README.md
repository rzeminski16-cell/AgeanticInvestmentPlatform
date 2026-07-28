# Static assets

Everything in this directory is **committed build output or a vendored library**. Nothing
here is fetched at runtime: the application is local-first and must work with no internet
connection, and a CDN script tag on a page that can reach your database and your provider
credentials is a supply-chain risk taken for convenience.

| Path | Origin | Regenerate with |
|---|---|---|
| `css/app.css` | Compiled from `../styles/app.css` by Tailwind v4 | `npm run build:css` |
| `vendor/htmx.min.js` | `htmx.org` npm package, copied verbatim | `just vendor-js` |
| `favicon.svg` | Hand-written | — |

## Why the compiled CSS is committed

CI runs `ruff`, `mypy` and `pytest`, and needs Node for none of them. Committing the
stylesheet keeps it that way: a checkout is immediately runnable, and a Node toolchain is
required only by the person changing the styles.

The cost is that `css/app.css` can drift from the templates if someone adds a Tailwind
class and forgets to rebuild. `tests/test_web_pages.py` guards the common case by
asserting the stylesheet exists and is non-trivial; the honest mitigation is that
`just css` takes under a second, and `just watch-css` runs it continuously while you
work.

## Updating a vendored library

```bash
npm install                # or: npm update htmx.org
just vendor-js             # copies dist/ files into vendor/
```

Record the version and the SHA-256 in the commit message, so the provenance of a file
that is not built from source in this repository stays checkable.
