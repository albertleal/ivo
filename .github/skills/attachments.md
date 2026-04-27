# Skill: Attachment Protocol (Telegram)

You are speaking through Telegram. The chat layer can deliver files
(images, documents, audio) **only** when you declare them using this
exact protocol. Anything else is displayed as plain text and the user
sees no file.

## When to use it
Use this protocol whenever you want the user to *see* or *receive* a
local file: a generated chart, an existing image on disk, a PDF, a CSV
the user asked for, etc. Do NOT use it for files you only consulted
internally.

## Format
At the very end of your reply append a block exactly like this:

```
<attachments>
/absolute/path/to/file1.png
/absolute/path/to/file2.pdf
</attachments>
```

Rules:
- One absolute path per line. No quotes, no markdown, no commentary inside the block.
- Only files that already exist on disk. Never invent paths.
- The block must be the last thing in your message.
- Omit the block entirely when there are no attachments.

## Body
Above the block, write the natural-language message as usual. Refer to
the attachments without pasting paths in the prose:

> Aquí tienes el gráfico de equity que pediste, el drawdown se
> mantiene bajo el 4%.
> `<attachments>`
> `/Users/me/charts/equity.png`
> `</attachments>`

The chat layer will strip the block, send each file as a Telegram
attachment, and show the user only the natural-language part.
