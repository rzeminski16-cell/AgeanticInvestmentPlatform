"""Licensed end-of-day market data: bars, splits, dividends and share counts.

The only paid feed this platform uses, and the only one whose bytes have an expiry date.
Everything it archives is :class:`~aer.fetch.policy.RetentionClass.LICENSED` and purgeable
under ADR 0031; nothing derived from it leaves the machine, under ADR 0030 route 2.
"""
