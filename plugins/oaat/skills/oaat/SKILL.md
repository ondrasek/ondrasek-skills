---
name: oaat
description: Use when there are several items waiting on the user (decisions, open questions, clarifications, concerns, risks) or when the user asks to go through them, "walk me through", "what's pending", or "one at a time". Presents and discusses each item strictly one at a time instead of dumping a list.
---

# OAAT (one at a time)

Address everything waiting on the user without overwhelming them. The user has ADHD: limited working memory, easily overloaded by lists. Never show more than one open item at once.

## 1. Collect silently

Gather every pending item from the conversation, tool output, notes or files in scope: decisions, open questions, needed clarifications, concerns, risks, unverified assumptions. Dedupe and merge overlapping items.

If the project has a milestone tracker (`.cache/milestones.json` in the project root; read it with Read, it is written by the `milestones` plugin), its items that wait on the user count too: every milestone that is not `done` and is `blocked`, or whose `needs` names the user (`you`). Its `name` is the title and its `left` text the substance. Remember which milestone `id` an item came from. No file, no change.

Do not print the list. Say only how many there are, then start (for example: "5 items need you. First one:").

## 2. Order

Put items that block other items or work first, then by impact if left unaddressed, then the cheap and quick ones. Items that depend on an earlier answer wait until that answer is in.

## 3. Present one item

Answer with this template. Keep its labels, their wording and their order, so every reply has the same structure. Keep the reply short, about 8 lines in total.

```text
**Item N of M, type: Title**
**What it is:** one or two plain sentences, no background.
**Why it matters:** one sentence on the consequence.
**Options:**
1. First option, one line, with its trade-off.
2. Second option, one line, with its trade-off.
3. Third option, one line, with its trade-off.
**Recommendation:** Option N, because a few words of reason.
**One question:** a single, concrete question the user can answer in a few words?
```

- **Item N of M, type:** the type is decision, question, risk, and so on. The title has a few words.
- **Options:** one to three. Number every option 1., 2., 3.
- **Recommendation:** one line of its own, after the last option. It names the option by its number.
- **One question:** the only question mark in the reply.

Then **stop and wait**. Say nothing about other items, do not preview what is next, and ask one question.

## 4. Handle the reply

- Answer given: confirm it in one line, apply or record it, and move to the next item. Do not reopen a settled item unless new information contradicts it.
- The answered item came from a milestone: record the answer through the `milestones` skill in the same turn, silently (for example clear `needs`, set `blocked` to `doing`, or update `left`). If that plugin is not installed, skip this and do not mention it.
- User asks for more detail: explain just that, still on the same item, then repeat the question.
- User disagrees or the assumptions were wrong: say so directly, adjust, and restate the question.
- "Skip", "later", "not now": move it to a parked list and continue.
- "Stop" or "enough": stop at once and give the wrap-up.
- Tangent that is not part of the current item: park it and reply with only this line, using these exact words and naming the current item as X:

  ```text
  This may be a tangent from X. Continue or refocus?
  ```

  Add nothing else: no item header, no repeat of the item, no second question. This line is the one question of the reply. If the user says "refocus", show the current item again in the template; if "continue", follow the tangent first and return to the item afterwards.
- New items found while discussing: add them to the queue silently; do not interrupt.

## 5. Wrap up

When the queue is empty, or on request, give a compact recap with this template:

```text
**Decided:** each decision in one line.
**Parked:** items skipped or deferred.
**Next action:** the single most useful thing to do now.
```

With a milestone tracker, the next action is usually the milestone in `doing`, or the first one the answers just unblocked.

## Rules

- One item, one question, per message. No exceptions.
- Plain words, short sentences, key terms in bold. No walls of text.
- Be honest: if the user's assumption looks wrong, say so rather than agreeing.
- Do not make decisions for the user. A recommendation is fine, but wait for their answer.
