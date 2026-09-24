# P07/P08/P11 human listening harness - 2026-09-23

## Result

The deterministic blind A/B harness is ready. Human listening acceptance is
still pending and no candidate winner is claimed.

- `tools/listening_ab.py` creates a randomized A/B packet from project-owned
  retained media and rejects paths outside the selected project root.
- Candidate order and trial order are reproducible from an explicit integer
  seed.
- Listener-facing `packet.json` contains only neutral A/B aliases. Source paths,
  candidate identities, hashes, provenance, and the seed stay in the private
  `organizer-receipt.json`.
- Media aliases are same-volume hardlinks, so the sample packet does not copy
  the retained MP4 data. Treat `media/` as playback-only: do not edit or
  overwrite an alias because it references the retained file content.
- Every media item is SHA-256 checked when the packet is created and again when
  ballots are evaluated.
- The rubric requires separate 1-5 scores for voice similarity/reference
  stability, naturalness, timing, and mix, plus A/B/tie preference.
- Ballot parsing is fail-closed. Missing/incomplete ballots, duplicate reviewer
  IDs, packet mismatch, media tampering, too few valid votes, or missing human
  decision all keep `gate_passed=false`.
- The parser only reports descriptive blinded means and preference counts. It
  never selects a winner. A pass requires an explicit human-attested decision.

## Sample packet

Directory: `work/benchmarks/listening-ab/`

- `study-spec.json`: retained fixture input and expected media hashes.
- `packet.json`: listener-facing randomized packet.
- `organizer-receipt.json`: private seed, mapping, provenance, hashes, and known
  limitations.
- `ballot-template.json`: schema-conformant blank listener ballot.
- `decision-template.json`: blank human gate decision.
- `evaluation-no-votes.json`: expected fail-closed receipt with
  `status=pending_human_votes`, `valid_ballots=0`, and `gate_passed=false`.
- `media/`: zero-copy A/B/reference hardlink aliases.

The sample uses the retained 60-second real clean-talking-head reference and its
cold before/after outputs. It is valid for a broad blinded listening check, but
it changes more than one pipeline behavior and therefore cannot establish a
single causal improvement.

Both retained sample outputs used TTS batch size 1. No project-owned isolated
sequential-versus-CUDA-FP16-batch-4 audio pair was found, so this sample cannot
close P08 quality parity. P08 still needs a separately retained pair generated
from identical text, reference, seed/sampling settings, and mix.

## Gate contract

`gate_passed=true` is possible only when all of the following are true:

1. Packet and aliased media pass receipt/hash validation.
2. At least `minimum_completed_votes` unique human ballots are fully valid.
3. The decision file attests that the named human reviewed exactly that valid
   ballot count.
4. The human decision is `pass` and includes a non-empty rationale.

This boolean means the human listening gate was accepted. It does not identify
or automatically choose a winning implementation.

## Exact human steps still required

1. Recruit at least three independent listeners for the sample study. Do not
   show them `organizer-receipt.json`, candidate names, automated metrics, or
   prior quality claims.
2. Give each listener `packet.json`, `ballot-template.json`, and read-only access
   to `media/`. Each listener makes a private copy named
   `ballots/<reviewer-id>.json`.
3. On the same headphones/speakers and fixed volume, listen to the reference,
   then A and B. Replay as needed. Score both candidates independently for all
   four rubric questions, select A/B/tie, add optional notes, set a unique
   `reviewer_id`, and set `blinding_confirmed=true`.
4. After all ballots are final, parse them without a decision. A non-zero exit
   is expected because the human decision is still absent:

   ```powershell
   $ballots = Get-ChildItem work/benchmarks/listening-ab/ballots/*.json |
       Select-Object -ExpandProperty FullName
   uv run python tools/listening_ab.py evaluate `
       --packet work/benchmarks/listening-ab/packet.json `
       --ballots $ballots `
       --output work/benchmarks/listening-ab/evaluation-before-decision.json
   ```

5. Confirm the result has at least three valid ballots and no integrity or
   ballot issues. Only now may the human review owner open
   `organizer-receipt.json`, interpret the blinded results against the real
   candidate mapping, and decide pass or fail. The tool must not make this
   judgment.
6. Complete a copy of `decision-template.json`: set `decision` to `pass` or
   `fail`, provide `decided_by` and `rationale`, set
   `human_attestation=true`, and set `reviewed_valid_ballots` to the exact parsed
   valid count.
7. Produce the final gate receipt:

   ```powershell
   uv run python tools/listening_ab.py evaluate `
       --packet work/benchmarks/listening-ab/packet.json `
       --ballots $ballots `
       --decision work/benchmarks/listening-ab/decision.json `
       --output work/benchmarks/listening-ab/evaluation-final.json
   ```

8. For P08, first create and retain the isolated sequential/batch-4 pair noted
   above, add it as another trial, regenerate with a new frozen seed, and repeat
   the same blind process. For CP7/P17, repeat over a representative fixture set;
   this single 60-second trial is not full release listening evidence.

## Validation

- `uv run pytest -q tests/test_listening_ab.py` -> `9 passed in 0.75s`.
- Sample create command completed with seed `20260923` and verified all declared
  source hashes.
- Sample no-vote evaluation intentionally exited non-zero and wrote
  `gate_passed=false`, `status=pending_human_votes`.
- `fsutil hardlink list` confirmed the blinded A alias and retained source share
  one underlying file.
- No production `src/`, `config.yaml`, `PLAN.md`, or runtime defaults were
  changed.
