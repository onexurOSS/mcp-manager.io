# Write path feasibility spike: sales invoice reconstruction

Scope: one workflow, `propose_sales_invoice_reconstruction` and
`apply_sales_invoice_reconstruction` in `src/manager_mcp/corrections.py`. This is an
investigation. No behaviour was changed. Evidence is in
`tests/test_write_path_spike.py` (17 characterisation tests against an in-memory fake Manager
that keeps real state and can inject a failure at any request).

## Conclusion

**Safe multi-step write operations are not achievable as designed** for this workflow.

The workflow makes one create call and then one update call per receipt, with nothing tying
them together. When a call in the middle fails, Manager is left half changed, the caller is
told only about the failing call, and the tool's own error text tells the caller to retry,
which creates a duplicate invoice. Recovery is possible in some states but depends on a key
that is only in a local file, and one state (mixed) cannot be rolled back at all. Details and
proof follow.

## 1. The chain as it stands

| Step | Implemented by | What it captures |
|---|---|---|
| Manager state | `_propose_invoice_reconstruction` reads each cited receipt (`GET /receipt-form/{key}`) and checks the missing invoice key really returns 404 | The party, the missing invoice key, the total of the citing receipt lines |
| Proposed change | `propose_correction`, called at the end of the propose step | Validates the invoice body, returns `proposal_token`, `proposed_fields`, `contributing_transactions` |
| Explicit approval | The caller passes `proposal_token` and `fields` to a second tool, `apply_sales_invoice_reconstruction` | See below |
| API write | `_apply_invoice_reconstruction`: `apply_correction` (create), then `_reallocate` per receipt (update) | Audit entries with before and after state, one per completed write |
| Verification | `verify_invoice_balance` at the end | Invoice total, allocated total, outstanding amount |

**What the approval gate is.** It is a separate tool call, and propose never writes: the test
`test_propose_makes_no_writes_and_apply_is_a_separate_call` shows zero POST, PUT or DELETE
requests during propose. Propose cannot fall through to apply. But the gate is weaker than it
looks:

- `proposal_token` is a hash of the invoice fields (`_proposal_token`: the first 16 hex
  characters of a SHA-256 over resource, key and fields). It is not stored and not secret.
  Any caller that can compute it can apply without ever proposing:
  `test_the_approval_token_is_not_stateful_and_does_not_bind_the_receipts`. Nothing records
  that a human, or even a propose call, came first.
- The token covers the invoice body only. `receipt_keys` is a separate argument that apply
  never checks against the proposal. The same test shows apply repointing a receipt that
  belonged to a healthy, different invoice, because apply looks for any allocation line on the
  receipt and does not check that it references the missing key.

## 2. The API calls

The order is create then reallocate. There is no void step in this workflow (`snapshot_and_void`
is a separate tool). For N receipts, apply makes **1 + N writes** and `4 + 6N` requests in
total (16 for two receipts). They are independent HTTP calls with no transaction, no
rollback and no pending record. Observed sequence for two receipts:

| # | Request | Purpose |
|---|---|---|
| 1 | `POST /sales-invoice-form` | Create the missing invoice (write 1) |
| 2 to 6 | `GET receipt rc-a`, `GET invoice new`, `GET receipt rc-a`, `PUT receipt rc-a`, `GET receipt rc-a` | Find the line, check the target exists, re-read, repoint (write 2), read back |
| 7 to 11 | The same five requests for `rc-b` | Write 3 |
| 12 to 16 | `GET invoice new`, `GET /sales-invoices`, `GET /receipts`, `GET rc-a`, `GET rc-b` | `verify_invoice_balance` |

Sales invoices have no known-keys list, so there is no read after the create. An audit entry is
written after each write and its follow-up read succeed, not before.

## 3. Partial failure: what was proved

The first call succeeds and a later call fails. Each row is a test; all pass and assert the
behaviour shown.

| Failure injected | Manager ends up | Caller is told | Audit log |
|---|---|---|---|
| First receipt update returns 500, 400, connection error or timeout | New invoice exists, no receipt points at it, both receipts still point at the missing key (an orphan) | Only the failing call, for example `Manager HTTP 500 for PUT /receipt-form/rc-a ... get_record a template, fix the body, retry once.` The created invoice's key appears nowhere. | One `create` entry with the new key |
| Second receipt update fails | Mixed: `rc-a` on the new invoice, `rc-b` still on the missing key | Only the failing call (`Manager.io is not reachable ... retry.`) | `create` and one `reallocate` |
| Create returns 200 with no `Key` | Invoice exists | `status: "partial"`, reason "Invoice create returned no Key", but the key is not given | Nothing |
| Create returns a non JSON body | Invoice exists | An exception from JSON decoding | Nothing, not even the create |
| Read after a successful receipt update fails | The update took effect | An HTTP error | No entry for that update (an unaudited write) |
| Final verification fails | Everything succeeded | An HTTP error, so a fully successful run looks like a failure | Complete |

None of the errors says that anything was created or that the state is inconsistent. Only the
create-without-key case returns a `partial` status, and it withholds the key. The tool's own
error text says to retry. **A plain retry creates a duplicate:** the original invoice key is
still missing, so propose returns the same token, and apply posts a second invoice
(`test_a_plain_retry_after_a_partial_failure_creates_a_duplicate_invoice`: `new-inv-1` and
`new-inv-2` both exist for one debt).

## 4. Recovery: what exists and what works

The order was create then update, so this is the only order tested. There is no stored
pending record and no tool that lists incomplete reconstructions. No MCP tool reads the audit
log (`read_events` is used only by tests), so the new invoice's key exists only in the local
JSONL file on the machine running the server.

| State | Recovery | Proved |
|---|---|---|
| Orphan (first update failed) | Void the new invoice with `snapshot_and_void` (needs the delete scope and a written justification). Manager returns exactly to its original state. | `test_recovery_from_an_orphan_by_voiding_it_restores_the_original_state` |
| Mixed (a later update failed) | Finish forward: call `reallocate_receipt_line` for the remaining receipt with the new key. `verify_invoice_balance` then reports fully paid. | `test_recovery_from_a_mixed_state_by_finishing_the_remaining_receipt` |
| Mixed, rollback | Not possible with the existing tools. Repointing back to the original key is refused (the target does not exist), and voiding the new invoice is blocked while a receipt references it. | `test_a_mixed_state_cannot_be_rolled_back_with_the_existing_tools` |
| Detection without the key | The original breakage is still reported by `find_broken_invoice_references` as if nothing was attempted. The orphan can be seen by `verify_invoice_balance` only if the key is already known. | `test_orphan_is_detectable_by_verification_but_nothing_links_it_to_the_failure` |

So recovery works when a person knows to look in the audit file, finds the create entry, and
picks the right forward or void path. Nothing in the tool response points them there.

## 5. Findings for the reviewer

These are recorded, not fixed. Findings 4 and 5 may be defects and not only design gaps, since
they contradict claims in the CHANGELOG, README and SECURITY.md that every corrective write is
recorded in the audit log.

1. No atomicity: 1 create and N updates as independent calls.
2. Failures raise, so the caller never receives a structured partial result naming what
   succeeded.
3. The error text advises retrying, and a retry duplicates the invoice. There is no
   idempotency check before the create.
4. A successful update can go unrecorded (the audit entry is written after the read back), and
   a create whose response cannot be parsed is unrecorded.
5. A fully successful run is reported as a failure if the final verification read fails.
6. The approval token is stateless and does not bind `receipt_keys`, and apply does not check
   that a receipt's allocation still refers to the missing invoice.
7. The audit log is not readable through any tool.

## 6. Minimum change that would make partial failure detectable and recoverable

Not implemented here. This is a decision for the reviewer.

1. Before the first write, record a pending reconstruction (a correlation id, the invoice
   fields, the receipt keys and each step's status) in the same audit store, and update it after
   every step. Mark it complete only when verification succeeds.
2. Catch failures inside apply and return a structured partial result: the new invoice key,
   which receipts were repointed, which were not, and the pending record's id. Do not tell the
   caller to retry.
3. Make apply idempotent: before creating, check for an invoice from a pending record with the
   same correlation id or token, and resume it instead of posting again.
4. Add one read tool that lists pending or incomplete reconstructions, so detection does not
   depend on reading a file on the server host. It can also offer the two recoveries that
   already work: finish the remaining receipts, or void an orphan.
5. Bind the token to `receipt_keys` and re-check, at apply time, that each receipt line still
   references the missing key.
6. Write the audit entry for a write as soon as the write succeeds, before any follow-up read.

Items 1, 2 and 3 are the minimum: without a record written first, a crash between calls leaves
nothing to detect.

## 7. Reproducing

`uv run pytest tests/test_write_path_spike.py -q` (17 tests). The fake Manager in that file
models receipts, sales invoices and their list endpoints, applies writes to its state, and
takes failure rules (a predicate over the method, path and current state, and a response or
exception). Any request it does not model fails the test, so the call sequence in section 2 is
exact and pinned by `EXPECTED_HAPPY_PATH_CALLS`.
