# Wireshark filter snippets (original)

These are *starting points*. Adjust IPs/ports/TEIDs for your environment.

- NGAP: `ngap`
- NAS-5GS: `nas_5gs`
- PFCP: `pfcp`
- GTP-U: `gtp && gtp.message_type == 255` (example; verify your dissector fields)
- HTTP/2 (SBI): `http2`

Tips

- For SBA, correlate with TLS SNI / ALPN and server certificates.
- For PFCP, follow the association lifecycle before looking at rule updates.
