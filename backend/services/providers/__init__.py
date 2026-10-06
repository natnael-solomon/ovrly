"""Outbound provider clients for the evidence stages (BE-09, #27).

Every client takes an ``httpx.AsyncClient`` from the caller, so tests inject a transport
that replays synthetic cassettes and no test ever reaches a real provider.
"""
