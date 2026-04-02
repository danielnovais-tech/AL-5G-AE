# Protocols cheat sheet (original)

## NGAP (N2)

- Between gNB and AMF.
- Look for: initial UE message, PDU session resource setup, AMF UE NGAP ID mismatch.

## NAS (N1)

- UE ↔ AMF signaling: registration, security mode, identity, session establishment triggers.
- Typical failures: security mismatch, timer expiry, rejected cause codes.

## PFCP

- SMF ↔ UPF control.
- Typical failures: association missing, rule install rejected, heartbeat timeout.

## GTP-U

- User plane. If session is up but no traffic flows: check TEIDs, routing, firewall/ACL, MTU.

## SBI (HTTP/2)

- AMF/SMF/NRF/PCF/UDM/AUSF service calls.
- Typical failures: 503s due to discovery/overload, TLS handshake, HTTP/2 stream resets.
