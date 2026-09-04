"""The artifact origins, named once.

Five stages filter on ``source_type`` ("what kind of thing is this") and each
used to carry its own copy of the list. Adding an origin then meant finding every
copy — miss one and the new documents are extracted but never indexed, or indexed
but never on the timeline. These tuples are the single place the answer lives.

* ``email``       — a message body; text is in ``body_clean``
* ``attachment``  — a file that arrived on an email
* ``disk_file``   — a file from the E-drive corpus; its folder names the property
* ``upload``      — a file a user added from a property page; that page names
                    the property, so like ``disk_file`` it skips Opus 5 segregation

``source_types`` (plural, on the artifact) is the additive list of *every* origin
the same bytes arrived from; ``source_type`` is the primary one.
"""
from __future__ import annotations

#: Files with bytes to extract text from. Not emails — their text is the body.
DOCUMENT_SOURCE_TYPES = ("disk_file", "attachment", "upload")

#: Everything that carries text worth indexing and placing on a timeline.
ALL_SOURCE_TYPES = ("email",) + DOCUMENT_SOURCE_TYPES

#: Origins where the property is known at arrival and no model call is needed.
PLACED_BY_LOCATION = ("disk_file", "upload")
