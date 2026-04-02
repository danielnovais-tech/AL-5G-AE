# 5G Core (5GC) architecture – operator notes (original)

## Network functions (quick intent)

- **AMF**: access + mobility; terminates N1/N2; registration, reachability, mobility events.
- **SMF**: session management; creates/modifies PDU sessions; controls UPF via PFCP.
- **UPF**: user-plane forwarding; enforces QoS/ULCL; reports usage.
- **NRF**: service registry + discovery for SBA.
- **PCF**: policy decisions (QoS, charging rules);
- **UDM/AUSF**: subscriber data + authentication.
- **NSSF**: slice selection assistance.

## Common “where to look” mapping

- UE can’t register → AMF logs + NGAP/NAS (N2/N1), AUSF/UDM reachability.
- PDU session fails → SMF logs + PFCP to UPF; check DNN/S-NSSAI mapping and policy.
- Data path broken after session up → UPF forwarding rules/QoS; check GTP-U paths and routing.

## SBA reminders

- SBA control-plane is usually HTTP/2 + JSON over TLS.
- Most outages are boring: DNS, TLS certs, MTU, routing, or a stuck connection pool.
