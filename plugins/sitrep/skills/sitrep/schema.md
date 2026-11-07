# data.js format

`DIR/data.js` must assign one object to `window.SITREP`:

```js
window.SITREP = {
  title: "Migration status",                // required, short
  summary: "Two decisions block the rest.", // one sentence on where things stand
  generated: "2026-10-25 14:30",            // free text
  next: { title: "Decide on database", why: "Unblocks 3 other items." }, // optional
  milestones: { title: "To release 1.0", done: 1, total: 4, current: "Storage layer", needsYou: 1, blocked: 1 }, // optional: progress strip
  items: [
    {
      id: "db",                    // unique, used by dependsOn
      type: "decision",            // decision | question | risk | concern | blocker | other
      title: "Pick the database",  // few words
      detail: "Postgres vs SQLite for the sync service.",
      why: "Schema work cannot start until this is settled.",
      options: ["Postgres: scalable, more ops", "SQLite: simple, single writer"],
      next: "Ask the user which constraint matters more.",
      owner: "me",                 // optional
      impact: 3,                   // 1 low .. 3 high
      urgency: 3,                  // 1 low .. 3 high
      status: "open",              // open | blocked | parked
      dependsOn: []                // ids that must be settled first
    },
    {
      id: "schema", type: "blocker", title: "Schema design",
      detail: "Cannot start before the database is chosen.",
      impact: 2, urgency: 2, status: "blocked", dependsOn: ["db"]
    }
  ]
};
```

Notes:
- Only `title` and `items[].title` are essential; every other field is optional and defaults sensibly (impact and urgency default to 2, status to `open`, type to `other`).
- Items are numbered `#1`, `#2`, ... by priority (impact x urgency), parked last. Numbers appear in the matrix, map and cards, so refer to items by them in chat.
- Unknown `dependsOn` ids and self-references are ignored; cycles are tolerated.
- `milestones` is optional and comes from the project's milestone tracker (`.cache/milestones.json`, see SKILL.md step 1): `done` and `total` count milestones, `current` is the name of the one in progress. Leave it out when there is no tracker.
- Valid JavaScript, not JSON: write it as `window.SITREP = {...};`.
- Keep `id`s stable when regenerating into the same `DIR`: the page stores comments and resolved flags per `id` in the browser.
- The report exports feedback as `{"sitrep", "exported", "for_claude", "items": [{"id", "n", "title", "comment"?, "resolved"?}]}`.
