"""Studies that use the chat agent as an instrument.

The agent itself is in `nl2sql`. Nothing in this package modifies it: the evals
drive the public HTTP surface and read the event stream, exactly as any other
client would. That separation is deliberate, so a result here is a statement
about model behaviour rather than about a private code path.
"""
