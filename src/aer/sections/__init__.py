"""Report sections, resolved from data and rendered generically.

**There is no list of section keys in this package, and there must never be one.** The
product requires that an operator can author their own report section as a natural-language
skill file and have it appear in the report. Whether that is cheap or a rewrite is decided
by whether the content model is data or code — so it is data, and a test scans the source
tree to confirm no enum, list or dispatch table has quietly appeared.

Two modules:

* :mod:`aer.sections.registry` decides *which* sections apply to a request, in what order,
  by querying ``section_definitions``.
* :mod:`aer.sections.render` turns a section's structured content into Markdown by walking
  its ``output_contract``, with no per-section template.

The second is the load-bearing one. A renderer that needed a template per section would
mean "add a section" implied "write a template", and a user-authored section has nobody to
write one.
"""

from __future__ import annotations
