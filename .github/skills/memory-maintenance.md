# Skill: Memory Maintenance

You operate with a per-agent memory file at `.ivo/memory/<agent>.md`
(e.g. `.ivo/memory/ceo.md`). The orchestrator reads this file before
every turn and injects its contents into your system prompt under a
`<memory>...</memory>` block. You see it; the user does not.

You also see `.ivo/memory/chat-context.md` — that is the **host project's
shared user/channel context**, not yours to edit unless the user explicitly
asks. Treat it as read-only by default.

## Your job

Keep your own memory file accurate, compact, and useful — like a working
notebook, not a chat log.

### What belongs in memory

- Durable facts about the project, the user's preferences, and your current plans
- Open threads, decisions made, and their reasoning
- Pointers to important files, commands, and conventions
- Lessons learned (mistakes you should not repeat)

### What does NOT belong

- Verbatim conversation transcripts
- Greetings, pleasantries, or one-off acknowledgements
- Tool output, raw logs, stack traces, or large code blocks
- Information that is already in source code or other documentation
  (link to it instead)

## How to update

You have file-edit tools available (`edit`). Use them directly to update
`.ivo/memory/<your-agent>.md`. Prefer:

1. **Replacing a section** — find the relevant `## heading` and rewrite it
   in place. Do not append a new dated entry just to record progress.
2. **Pruning** — when a section grows past ~30 lines or contains stale
   items, condense it. Drop completed threads. Merge duplicates.
3. **Promoting facts** — when a one-off detail becomes a recurring pattern,
   move it into a top-level section.

Make the edit only when there is something genuinely new and durable to
record. A normal turn with no learning produces zero writes.


Evaluate after every edit the integrity of the full document.

## Integrity rules (hard requirements)

After every edit the file MUST be self-consistent. Re-read it end-to-end
before saving. The following are forbidden:

1. **No duplicate notes on the same topic.** If a bullet about topic X
   already exists, edit that bullet in place — do not add a second one.
   One topic ⇒ one line (or one section).

2. **No negative / invalidating bullets.** Never write a line whose only
   purpose is to cancel an earlier line, e.g.:

       - The user prefers to be called "daddy".
       - User prefers not to be called "daddy" anymore.   ← FORBIDDEN

   The correct fix is to **delete the stale bullet entirely**. If a new
   positive fact replaces it, write only the new fact:

       - The user prefers to be called by their first name.

   If there is no replacement fact, just remove the stale line and add
   nothing.

3. **No contradictions.** Two bullets must not assert opposite things.
   When facts change, the old bullet is removed; the new bullet stands
   alone.

4. **Decide which version stays.** If you encounter conflicting prior
   notes, pick the most recent / most specific one, rewrite it cleanly,
   and delete the rest in the same edit.

### Self-check before saving

- Does any bullet contradict, negate, or duplicate another? Fix in this
  same edit.
- Could a reader who only sees the final file infer the wrong thing
  because a stale bullet is still present? Delete the stale bullet.
- Is the smallest possible diff applied? Replace a line, don't append
  a correction.



## Style

- Bullets and short sentences. No prose.
- Present tense for current state, past tense for decisions.
- File paths in backticks. Numbers concrete.
- Headings stable across edits so future-you can find things.

## Self-check before writing

Ask yourself:
- Will this matter next week? If no, do not write it.
- Is it already in the file under a different heading? If yes, edit there.
- Could a one-line update replace ten lines? Do that.
