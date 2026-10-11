# Bounded Fair Play snapshot transport

The Daily bot used a full, unfiltered all-ref checkout. This also transferred
historical encrypted checkpoint, compute and owner-audit blobs. Because every
encryption produces new ciphertext, these histories can grow much faster than
the source branch and cannot benefit much from Git delta compression.

Daily checkout now requests `filter: blob:none` while preserving full commit
history (`fetch-depth: 0`). Current source files and historical files requested
by economy/history functions remain available through Git's lazy blob fetch.
This is supported by the existing official checkout action.
Compute workers use the same blob filter while retaining their single pinned
code commit. Lazy reads avoid downloading other workers' snapshot blobs merely
to refresh the shared exchange ref.

Checkpoint, compute and owner-audit stores need the latest snapshot tree, not
old ciphertext. Their explicit fetches now request depth one and no tags.
A `+` refspec refreshes only the **local remote-tracking ref** across shallow
boundaries. All remote pushes remain ordinary fast-forward pushes. Concurrent
compute writes still fail and retry against the latest tree; other workers'
files are preserved. Remote commits and old evidence are not deleted or
rewritten. Encryption and retention formats do not change.

Real temporary Git tests verify:

- Old ciphertext ancestors are absent from a fresh snapshot reader.
- A competing writer between read and push triggers recovery without losing
  either writer's files.
- Checkpoints restore after a fresh checkout and persist another update.
- Owner evidence restores and remains readable after another save.
- Filtered cloning retains all main commits and can read an older economy-like
  file without fetching unrelated historical ciphertext blobs.

This removes unnecessary historical transfers; it does not guarantee a fixed
startup or scan duration. No production case data is used in these tests.
