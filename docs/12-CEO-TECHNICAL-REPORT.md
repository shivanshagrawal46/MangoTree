# Technical Report to the CEO

**To:** Rakesh Bhargava, CEO, RKB Consulting Group
**From:** Shivansh Agrawal, System Development
**Date:** 1 September 2026
**Subject:** Engineering challenges encountered and resolved building the property intelligence system

---

## Executive summary

We are building a system that reads every email, document and photograph connected
to our 15 renovation loans, understands which property each piece of information
belongs to, and answers questions about any deal with citations back to the
source.

Nine substantial engineering problems were encountered and solved. Two remain
open and are described honestly at the end.

**The single most important result so far is not technical.** While testing how
the system decides which emails matter, it surfaced correspondence that had been
sitting outside our review process entirely — including a lawsuit naming RKB as a
defendant with a missed filing deadline. Details in section 4. That finding alone
demonstrates why this system is worth building.

---

## 1. Capturing mail that is invisible to conventional tools

**The problem.** You send business mail from your Gmail account using the
`rakesh@mtreh.com` dropdown. Those messages are stored only in Gmail's Sent
folder. They never touch the Exchange mailbox, so no Outlook-based tool can ever
retrieve them. Equally, mail sent directly from Outlook never appears in Gmail.
Neither mailbox is a complete record of what you sent.

Worse, the "From" line on those messages says `rakesh@mtreh.com`, so a system
reading headers alone would misclassify them.

**The solution.** We connect to both providers and decide whether a message was
sent or received by **the folder it was found in**, not by what its headers
claim. A message in the Sent folder was sent, regardless of which address appears
on it. Conversations are then stitched back together across both providers using
the underlying message identifiers, so a thread that began in Outlook and
continued in Gmail reads as one conversation.

**Why it matters.** Without this, roughly half of your outbound correspondence
would be missing, and the system would confidently produce incomplete answers —
which is more dangerous than producing none.

---

## 2. Keeping properties separate

**The problem.** A single email frequently discusses two or three properties:
*"Varnum tile is finished, and Chita Court needs $4,000 for the roof."* If the
system stores that as one undivided record, a question about Varnum can return
Chita Court's figures. In a lending business, a number attached to the wrong
property is worse than no number at all.

A related hazard: we hold loans on **904 and 910 Bayshore Drive** — two different
properties, two different loans, same street. Any system that treats "Bayshore"
as one thing will silently merge two loan files.

**The solution.** Multi-property emails are split into segments before storage,
and each segment is tagged with the property it concerns. When you open the
Varnum workspace, the property filter is applied *during* the search itself, not
by discarding unwanted results afterwards. The distinction is important: filtering
afterwards means the results are already contaminated and we are relying on
tidying up, whereas filtering during the search means other properties are never
candidates in the first place.

904 and 910 Bayshore are registered as deliberately separate entries with a
standing instruction never to merge them, and this is verified by test.

**Why it matters.** Property isolation is the guarantee the whole system rests
on. It is enforced structurally rather than by careful behaviour.

---

## 3. Reading documents that ordinary software cannot

Four separate obstacles, each solved:

| Obstacle | Resolution |
|---|---|
| Scanned documents with no searchable text | AI vision reading, 1,313 pages processed |
| 47 pages our primary AI **refused** to read | Automatic switch to a second, independent AI provider |
| 7 Word documents in a 1990s format modern software cannot open | Custom-built reader for the legacy file structure |
| 109 email attachments extracting no text at all | Defect identified and corrected |

The refusal case deserves explanation. Title reports and insurance policies
contain large amounts of personal information, and our primary AI provider's
safety systems declined to transcribe 47 such pages. These were among the most
important documents we hold. The system now automatically escalates refused pages
to a second provider with different policies, and any page both refuse is flagged
for a human rather than silently stored as blank.

**Processing speed** was also addressed: document reading was originally taking
20 seconds per page, or about seven hours for the corpus. By processing pages
concurrently this is now 3.1 seconds per page — roughly six times faster, around
70 minutes for the full corpus.

**Why it matters.** A document the system cannot read is a document that does not
exist as far as any answer is concerned — and it fails invisibly. Every one of
these cases would have produced confident answers with critical evidence missing.

---

## 4. Correspondence that was being filtered out — the most significant finding

**The problem.** To keep personal mail and marketing noise out, the system only
ingested messages involving people on our known contact list. This is a sensible
default. Its failure mode is not.

Lawyers, accountants and payoff departments are, by definition, people who were
not on a contact list built around the builder relationship. The filter was
therefore excluding precisely the highest-stakes correspondence we have.

**What was found when we examined the excluded mail:**

- **31 July 2026** — `URGENT: Lawsuit Filed — RTLF-DC IIB LLC v. 1512 Varnum
  Street NW LLC et al.`
- **11 June 2026** — `URGENT — RKB Served as Defendant in Case 2026-CAB-000806 —
  Missed Answer Deadline`
- `~$85,000 Vacant Property Tax PAID Under Foreclosure Duress`
- A **lis pendens and default notice** on 910 Bayshore Drive from Florida counsel
- Our CPA escalating **Builder's Risk Insurance** across four emails, ending in
  "Final Notice", with no resolution visible in the record
- An **$81,523.04** ACH payment confirmation from two DC government officers

**The solution.** 235 external correspondents were identified across 589
excluded messages, presented for your review with subject lines and message
counts, and 20 were approved and added to the permanent contact registry —
including Gallagher Law, Quinn Legal, KC Wilson, Sayles Legal, our CPA, the DC
tax office, and two senior-lender payoff desks.

**Why it matters.** This is the clearest demonstration of the system's purpose.
A lawsuit naming RKB as a defendant, with a missed filing deadline, was outside
our review process. The system found it in the course of a routine configuration
check.

---

## 5. Preserving the origin of every document

**The problem.** The same PDF often exists in two places: attached to an email,
and saved in a folder on the E: drive. The email copy carries context — who sent
it, when, in which conversation, and what the covering message said. The folder
copy is an anonymous file with a name.

Our original build loaded the E: drive first. Because duplicates are detected by
content, the anonymous copy claimed the document and the email version was
discarded as a duplicate. We were systematically discarding the more valuable
copy.

**The solution.** The build order was inverted at your direction: **email first,
then disk**. The copy carrying full context now wins, and the folder copy simply
records a second known location. This was worth rebuilding the corpus for.

**Why it matters.** For a title report or an executed amendment, *who sent it and
when* is often as legally significant as the contents.

---

## 6. Making retrieval accurate rather than approximately relevant

**The problem.** A fragment of text pulled out of a 60-page title report is
nearly meaningless in isolation. Searching such fragments produces answers that
are plausible-sounding and wrong.

**The solution.** A three-layer context system. Every fragment carries a short
AI-written description of where it sits in its document; every document carries a
description of its role in the deal, including which deal structure applies; and
every property carries a live summary injected at the moment a question is asked,
so it always reflects current facts rather than facts frozen at filing time.

Achieved 99.4% coverage at an average of 116 words of context per fragment,
within the 100–150 target you set.

**Why it matters.** This is the difference between a system that finds documents
and one that answers questions correctly.

---

## 7. Preventing invented answers

Financial and legal analysis is worthless if the system can fabricate.
Protections built in:

- Every claim carries a citation to a specific source document
- Quotations are verified **word-for-word** against the original before being
  accepted; unverifiable quotes are rejected rather than reported
- Instructions you give the system are stored exactly as written and reinserted
  verbatim, never paraphrased
- High-stakes conclusions are reviewed by several independent AI models before
  being presented
- Attorney work product is marked privileged and excluded from general retrieval

---

## 8. Cost and safety controls

Two incidents on 31 August prompted permanent process changes.

First, a question was answered in a way I treated as authorisation, and I ingested
85 emails without explicit approval. This was rolled back completely, verified
clean, and the messages re-queued so nothing was lost.

Second, I discovered afterwards that our own records could not prove whether a
set of broken document links pre-dated my work. I reported this as unresolved
rather than assuming I was blameless.

**Standing rules now in force:**

1. Nothing destructive or billed runs without explicit confirmation — twice for
   destructive operations
2. Every operation runs first as a preview showing exactly what it would do
3. Costs are estimated and approved **before** any billed processing begins
4. Answering a question is never treated as permission to act
5. Nothing is deleted without first being re-queued or accounted for

The AI review of every email is the most significant recurring cost in the
design. It will be measured and brought to you for approval before it runs, not
after.

---

## 9. The Microsoft outage of 31 August — investigated and cleared

**What happened.** During setup of Outlook access, the mailboxes of yourself and
JP Sir became unreachable. Neither could send nor receive. Because this coincided
with our configuration work, we treated our own system as the prime suspect and
investigated it as such.

**Conclusion: our system was not the cause,** and this is supported by evidence
rather than assertion.

| Evidence | Finding |
|---|---|
| Permission held | Read-only. No ability to send, delete or modify existed |
| Our first mailbox read | **Failed** 85 seconds after sign-in — before we could affect anything |
| JP Sir's mailbox | Access **refused** by Microsoft; we never reached it |
| Your account status | Healthy throughout — authentication kept working normally |
| Shivansh's mailbox | Unaffected, ruling out a company-wide fault |

**Confirmed cause.** Microsoft incident **MO1465074**, "Users may experience
issues when utilizing multiple Microsoft 365 services." Microsoft's own stated
root cause: *"An issue within a core authentication configuration used by multiple
Microsoft 365 services."* Exchange Online affected across **all connection
methods**, alongside SharePoint, Teams, Purview and Defender. Microsoft moved to
revert a recent infrastructure update.

**The timing is decisive.** Microsoft's incident began at 11:08 AM EDT on 31
August — **more than four hours before** we attempted our first connection. Our
failed requests were an early symptom, not a cause.

**Action taken.** The application registration was removed as a precaution. It
holds no permissions and can be recreated in minutes once Microsoft's service is
restored. No configuration changes were made to any mailbox, licence or account.

---

## Current status

**Complete:**

- 15 properties and 37 people registered with verified contact addresses
- Document reading pipeline, including scanned, refused and legacy formats
- Three-layer context system at 99.4% coverage
- Property isolation, enforced structurally and verified
- Full rebuild plan approved and documented

**Open:**

| Item | Status |
|---|---|
| Outlook access | Blocked by Microsoft incident MO1465074, outside our control |
| Full corpus rebuild | Awaiting Outlook so both mailboxes load together |
| Two homeowner email addresses | Jason Tennstedt and Tisha Elliott — needed for automatic matching |

**Decision required from you:** approval of the AI processing cost, which will be
presented as a measured figure once mail volumes are known.

---

## Assessment

The difficult and unusual problems have been solved: capturing mail no
conventional tool can see, keeping 15 properties rigorously separate, reading
documents other software cannot open, and preventing invented answers.

The remaining work is largely sequential execution of an approved plan, gated on
Microsoft restoring service to two mailboxes.

The strongest argument for the system is what it found before completion. A
lawsuit naming RKB as defendant with a missed filing deadline, an $85,000 tax
payment made under foreclosure duress, and a lapsing insurance policy were all
outside the review process. They are now inside it.
