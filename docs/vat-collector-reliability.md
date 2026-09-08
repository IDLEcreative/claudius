# Quarterly financial evidence collector reliability

## Outcome and current claim

Outcome VAT-01: collect financial email attachments without filing unrelated
vendor reports, losing distinct generic-named invoices, or duplicating content.
The existing scheduled email router and daily archiver are the active entrypoints.
Owner, release and cleanup: root task. Independent review: initial review found two blockers (folder resolution outside
the lock and same-batch retries after uncertain writes). Both now have fixes and
regression tests; final immutable review is pending.
Current claim: local candidate; no deployment or live user proof.

The complete requested job also includes outgoing invoices, linked/body-only
receipts, entity review, bank reconciliation and an accountant-ready pack. Those
are not implemented or certified by this attachment repair. Email arrival date is
still a filing convenience, not a verified tax point. No VAT calculation is made.

## Repair contract

- Both collectors use one subject/filename evidence selector. Sender/vendor/VIP
  matches alone cannot file attachments. Scanned receipt images remain supported;
  decorative or unnamed inline images are excluded.
- Root and nested MIME attachment parts are examined.
- One shared account-specific file lock serialises folder resolution, listing and upload.
- Duplicate identity is content checksum and size within the destination folder,
  including existing Drive files. Different documents named `invoice.pdf` survive.
- Drive lookup failures abort rather than create replacement folders or files.
- Upload success requires independent checksum, size and destination readback.
- A failed attachment prevents the containing message being marked processed.
- Originals, sharing, account configuration and schedules are unchanged.

## Supporting validation

Run `python3 -m unittest discover -s __tests__ -v`, Ruff on the two entrypoints,
the two financial helper modules and tests, and mypy on the financial helpers.
The tests execute actual routing and daily entrypoints with external effects
replaced, and cover false-positive reports, VIP artwork, inline images, scanned
receipts, generic filename collisions, legacy duplicates, concurrent collectors,
partial failures, unknown upload outcomes, pagination and wrong-folder readback.
Python 3.10 syntax is checked against the deployed interpreter version.
28 tests pass. These are controlled local tests, not real end-to-end proof.

## Optimisation and limits

For F existing folder files and A attachments per message: one serial Drive scan,
O(F + A) checksum lookups, plus O(total attachment bytes) hashing. Retained memory
is at most 20,000 checksum/size pairs and one downloaded attachment. Drive listing
is capped at 20 pages of 1,000; each file is capped at 25 MiB and each filing call
at 100 attachments. Exceeding a limit aborts or returns a failure, never success.
For M messages the scan is repeated under the lock to observe other writes, giving
O(MF + total bytes) work. There is no persistent stale listing cache. At most
20 listing calls, A downloads, A uploads and A readbacks per filing call, plus
authentication/folder resolution. Active filing concurrency is one per account.
The lock is local-host only. Tests compare 10/100 rows and exercise the page cap.

## Release checkpoint and next proof gate

At 2026-09-08 the deployed collector files matched the original tracked source.
The production checkout contains unrelated local changes; never pull/reset it.
Repository branch protection returned “Branch not protected”; the repository ruleset list was also empty. Do not weaken or invent a protected merge path.

After immutable review and an authorised merge path, deploy only the two helper
modules and two collector entrypoints, retaining exact original-file backups.
No service restart or cron changes are needed for scheduled Python entrypoints.
Verify source hashes, execute offline tests with the deployed Python, then prove
fresh ordinary email-to-Drive capture, duplicate prevention and failure handling
through the actual user/provider surfaces. Require user-visible and provider
readback and recording. Do not send test emails or accountant messages without
the corresponding authority. Remove disposable fixtures and verify absence.

## Observations and remaining scope

- `email-router.py` and `invoice-archiver.py`: inherited monoliths remain over
  300 lines; this patch removes duplicated capture code, but is not a full router
  refactor. All newly added code files remain below 300 lines.
- `email-router.py:search_messages` and `invoice-archiver.py:search_messages`:
  message discovery still reads one page. Full historical completeness unproved.
- `email-router.py:process_new_emails`: polling selects unread messages and keeps
  a bounded processed list. Failed messages remain eligible only while selected;
  no durable retry queue is claimed. Push history can advance past failed items.
- `parse_email_date` / `get_quarterly_folder` in both entrypoints: filing uses the
  email date, invalid dates fall back to now, and February labels use day 28.
- `lib/financial_attachments.py`: selection is a heuristic. Body-only/portal
  documents, legal entity, invoice direction and VAT eligibility require review.

No observations authorise unrelated changes, bank-data mutation or external sends.
