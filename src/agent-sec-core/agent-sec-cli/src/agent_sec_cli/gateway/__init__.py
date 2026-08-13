"""Secret gateway proxy: mitmproxy data plane assets.

This package holds the pieces that run inside (or alongside) the mitmproxy
subprocess the daemon supervises. Nothing here may be imported by the daemon's
own request path -- ``credential_inject_addon`` in particular is loaded by
mitmproxy's *bundled* interpreter and therefore must stay standard-library
only.

The injection mechanism is provider-agnostic: which hosts, which header and
which fake/real pair all come from config, so supporting a new API key is a
config edit rather than a code change.
"""
