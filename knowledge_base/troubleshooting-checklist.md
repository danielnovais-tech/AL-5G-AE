# Troubleshooting checklist (original)

## Before diving into details

- Confirm scope: single UE vs all UEs; single slice/DNN vs all; single site vs region.
- Identify “first bad”: deployment change, cert rotation, DNS change, routing change.
- Check time sync: NTP drift can look like auth/TLS failures.

## Registration failures

- AMF: reject causes, NAS security mode, timer expiries.
- AUSF/UDM: auth vectors, timeouts, TLS errors.
- gNB ↔ AMF: SCTP stability, NGAP errors.

## PDU session failures

- SMF: DNN/S-NSSAI mismatch; policy rule install failure.
- UPF: PFCP association; rules rejected; heartbeat.
- Data plane: GTP-U reachability; TEID mismatch; routing/MTU/ACL.

## When using logs

- Extract: timestamp range, UE identifiers (SUPI/GUTI), session identifiers (PDU Session ID), and relevant NFs.
- Prefer “request → downstream call → response” chains.
- If you must paste logs, remove secrets/tokens.
