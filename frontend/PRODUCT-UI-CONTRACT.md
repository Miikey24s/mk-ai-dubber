# VI Dubber product UI contract

Status: **M1 contract freeze v1 — project-local, no shared runtime release**. Current implementation status and open acceptance gates live in [PLAN](../PLAN.md#ui); this file retains the identity/readiness contract.

This contract defines the state and identity that Library/Watch/Review work must preserve. It does not replace pipeline manifests, backend job state, or `PLAN.md` acceptance gates.

## Identity and time

The long-lived product model separates media identity from a processing attempt:

| Concept | Contract |
|---|---|
| Media | stable `mediaId`; one source item must not become a new library item for every retry |
| Rendition | `renditionId` + `revision` + language/backend identity when relevant |
| Processing attempt | existing job ID; useful provenance but not the media identity |
| Timeline | source-media seconds; segment/chunk timestamps and watch position use the same timebase |
| Artifact lineage | revision/fingerprint ties preview/final/subtitle/transcript back to the rendition that produced it |

Existing UI paths may use `job.id` internally, but it remains a processing-attempt identity. The implemented local catalog does not make that shortcut the durable media/rendition contract.

## Preview/final availability

Preview state is semantic, not just a badge:

| State | Play/download | Required UI behavior |
|---|---|---|
| `ready` | allowed | show exact chunk/time range and provenance |
| `processing` / `queued` | blocked | progress only; do not simulate final media |
| `stale` | blocked | explain that an edit invalidated this artifact; offer bounded rerender when available |
| `blocked` | blocked | surface QA/block reason and next action |
| missing/unknown | blocked | do not invent duration, file path, or readiness |

An edit that affects a segment invalidates the owning chunk/rendition lineage. A stale preview must never be played as if it were current. Final output is unavailable/stale until the required dependent assembly is valid again. A completed processing artifact or Final badge is not a QA-pass claim; Job12 currently demonstrates this distinction (see [PLAN](../PLAN.md#job12)).

## Representative navigation contract

M1/M3 use one representative flow:

```text
Library/search
  -> Watch(mediaId, renditionId, time)
  -> Review(same media/rendition, active segment/time)
  -> Watch(same media/rendition, exact return time)
```

State that must survive the round trip when applicable:

- `mediaId`, `renditionId`, revision;
- current source-media time and active segment;
- selected preview/chunk when it is still valid;
- subtitle/transcript visibility choice;
- Library query/filter/scroll context for the return path.

Changing to a different rendition must be explicit. The UI must not silently jump from a stale/failed rendition to another job or output.

## Reuse boundary

Reuse existing project components first: `VideoPlayer`, `LongformPreviewRail`, `SegmentReviewer`, the job lifecycle context, and existing edit/rerender calls. M1 does not add a new UI framework or a cross-project React component package.

Portable global semantics may map to roles such as `surface`, `text`, `muted`, `selected`, `focus`, `warning`, `error`, and `unknown`. Media/job/rendition semantics stay in this project.

## Deterministic M1 fixture

The representative UI fixture used for visual/interaction work must contain:

- one completed long-form media item with real persisted metadata;
- transcript/segments with one selected segment;
- at least three preview chunks covering `ready`, `stale`, and `blocked` states;
- one edit that demonstrates invalidation and selective rerender lineage;
- an explicit final availability state;
- real duration/timebase values from the fixture, never a placeholder duration.

Capture/QA widths: desktop `1440`, compact desktop/tablet `768`, and narrow `390` where the product flow remains usable.

## M1 acceptance oracles

1. stale/blocked preview cannot play or download as current output;
2. Watch → Review → Watch returns to the same media/rendition and exact source-media time within the player tolerance;
3. editing one segment invalidates only the dependent lineage and preserves unaffected ready chunks;
4. reload restores backend-persisted readiness state; browser-only state cannot override backend truth;
5. missing metadata does not fabricate duration/readiness;
6. keyboard/focus behavior remains usable at the representative widths;
7. export/download labels only advertise artifacts that exist for the selected revision.

M5 local catalog/view/recovery and offline benchmark implementation now exist. Their receipts remain PREP_ONLY and do not close product acceptance; remaining Library persistence, bookmarks/history, real-media recovery and search-performance gates follow [PLAN](../PLAN.md#current-queue) and the workspace scope. Do not replay the old M1 future-work wording as a new implementation order.
