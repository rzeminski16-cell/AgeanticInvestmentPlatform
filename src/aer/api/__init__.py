"""HTTP layer: the application factory, dependencies, error handling and routers.

Nothing in here computes a number or asserts a fact. It validates input, resolves
dependencies, calls a service and renders a result. Business logic lives in
``aer.services``; arithmetic lives in ``aer.calc``. A handler that grows a calculation is
a handler that has taken work from a layer where it would have been unit-tested.
"""

from __future__ import annotations
