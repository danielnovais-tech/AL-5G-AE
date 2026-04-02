# AL-5G-AE Knowledge Base

Place your 5G Core reference materials here. When started with `--rag-dir knowledge_base`, AL-5G-AE indexes **all `.txt` files** in this directory and retrieves relevant chunks for each question.

This folder ships with a small, **operator-created** starter pack so RAG works out of the box.

## Suggested content

- 3GPP specifications (e.g., TS 23.501, TS 23.502) — download from the 3GPP portal and save as `.txt` *if you have the right to use/share them*.
- Vendor troubleshooting guides (Nokia, Ericsson, Huawei, etc.) — extract relevant sections you are permitted to store.
- Internal runbooks, postmortems, known-issues databases, alarms dictionaries.

## Included examples (written from scratch)

These files are intentionally **not copied from 3GPP**; they are short summaries to demonstrate formatting:

- `ts_23501.txt` — architecture concepts and NF/interface reminders
- `ts_23502.txt` — high-level procedure summaries (registration, PDU session)
- `vendor_troubleshooting.txt` — vendor-agnostic troubleshooting patterns

## Quick start

```bash
python al_5g_ae.py --rag-dir ./knowledge_base
```

## Tips

- Prefer short sections with headings.
- Put error codes and “symptom → checks → fix” as bullet lists.
- Keep one topic per file when possible.
