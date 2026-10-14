# Windows MCP server (optional)

Drop `Sbroenne.WindowsMcp.exe` here.

Source: https://github.com/sbroenne/mcp-windows  (MIT)
A standalone .NET binary; it is NOT vendored into this repository because it
is third-party and redistributable only under its own licence.

Why it is not committed: ~57 MB binary, maintained upstream. Point this
directory at an upstream release instead of forking it.

Once the binary is present, `python preflight.py` will report
`[PASS] MCP server handshake succeeded` and Tier 3 can drive any Windows
application's UI. Without it, the MCP client stays off and the rest of Hey Jev
works normally.
