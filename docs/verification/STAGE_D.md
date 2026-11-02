# Stage D — isolated lifecycle evidence and limits

`tests/test_isolated_lifecycle.py` runs real source_reader/pipeline/policy/store/transaction and inbox approval against a TemporaryDirectory; its model is an explicit deterministic fixture. It invokes the actual OpenClaw project() function in a fresh standalone Node process, not a running OpenClaw host. All approvers are simulated. No production or private diary inputs.

Eight integration cases:

1. User source -> low-confidence evidence-linked pending claim -> no projection -> explicit simulated approval -> actual adapter projection -> fresh Python/Node process reload -> no persona/viewer/other-scope recall -> transaction rollback restores every canonical record list and pending candidate -> no projection. Logical state restored; transaction backups/audit attempts are preserved, and newly created empty files can remain under existing rollback semantics (not byte-identical directory deletion).
2. Secret plus malicious instruction -> no active projection; re-import skips unchanged source; secret absent from every new non-daily file.
3. Same-slot contradictory update -> pending evidence -> generic approval rejected -> old active remains, pending remains. This first failed and drove the C core inbox repair. No silent version winner.
4. Corrupt journal -> recover raises -> pre-existing fact bytes unchanged.
5. Invalid absolute/parent/drive paths -> rejected without writes.
6. Simulated PermissionError at the write primitive -> no canonical activation.
7. Cross-day source manifests carry -05:00 and +08:00 timestamps through real pointer resolution and explicit SUPERSEDES. A failed assertion exposed system-clock valid_to; canonical store now retains the newer verified source time separately from expired_at. Old/new evidence remains and actual adapter projects only the current version.
8. A real child process is killed after writing the first target in a two-file transaction while holding writer_lock. A fresh lock and two recovery passes produce both intended files and remove the journal; abnormal process exit is checked. No fake exception substituted for the kill.

Other relevant regression evidence in the full suite: duplicate events, overlapping writer refusal, transaction rollback/later-edit conflict, stored source tamper, damaged managed storage, assistant attribution, namespace/scope isolation, TTL expiry and dual-time pure-function boundaries. These are separate tests, not falsely attributed to the eight-case lifecycle.

**D local exit: GO to bounded E evaluation, simulated-host qualification only.** All eight cases pass. Prose logs without timezone retain their original precision; their exact historical timezone is not invented or certified. The tested revision is an explicit current change with source offsets, not arbitrary retrospective date extraction. Generic approval refuses conflicting slots; the adapter suppresses unresolved slot projection. A full interactive conflict-resolution interface and real-host trial are not claimed.

Rollback: no runtime deployment exists; abandon the isolated branch or revert its code commit. Temporary stores are disposable. The kill/recovery test retains no real data. No scoring or memory accuracy uplift is inferred from an echo fixture.
