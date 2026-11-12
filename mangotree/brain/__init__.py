"""The brain — one living file per property, kept current by events.

    new email / sent reply / call note / upload
        -> one Opus 5.5 read of (the property file + the new item)
        -> the file is updated, the activity log gets a line, to-dos open or close
        -> if a reply is owed, a draft is written and waits in the drafts list

    chat reads the same file, so it starts every conversation already knowing
    the property, holds the whole thread, and opens a document only when it
    needs the exact words.

One model (``config.MODEL``), one flow, nothing rebuilt from scratch each day.
"""
