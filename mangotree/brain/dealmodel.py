"""How RKB, ROI Blocks and the homeowner deal — the firm's basic rules, in one place.

This is Rakesh Sir's own statement of the model, as he dictated it and Shivansh
passed it on (recorded 2026-11-13). It is part of the shared core every model call
starts from (``prompts.CORE``), so the chat, the reply drafts, the property files,
the stage reading and anything built later (the money sheet, follow-ups) all reason
from the same rules.

The sharing of the markup (two points and six months' interest off the top to RKB,
then the remainder half to RKB and half to ROI Blocks, ROI's half paid in two parts) was
added the same day from Shivansh's explanation.

Change the rules HERE and nowhere else. Some things in the dictation were not
clear enough to state as rules; they are listed at the end as open, and the model
is told not to assert them.
"""
from __future__ import annotations

RECORDED = "2026-11-13"

DEAL_MODEL = f"""HOW RKB, ROI BLOCKS AND THE HOMEOWNER DEAL
These are the firm's standing rules, as Rakesh Sir stated them ({RECORDED}). Reason from them
on every deal. The signed papers of a particular deal (its note, its rider, its renovation
agreement) decide that deal: where they differ from a rule here, follow the papers and say
that they differ. Older deals, and deals where Wes or his company owns the house, may be on
different terms — check the deal's own note before applying these rules to it.

WHO SIGNS WHAT
* The homeowner (the property owner) signs the renovation agreement with ROI Blocks.
* RKB makes the loan. RKB underwrites it, funds it, holds the mortgage on the property, and
  approves each draw against work that has actually been done.
* A licensed contractor does the work. Where the contractor on site has no licence of its
  own, LP Remodeling is the contractor of record: it holds the Maryland Home Improvement
  Commission (MHIC) licence. ROI Blocks and LP Remodeling are sister companies, both owned
  by Wes Stone — treat them as one counterparty.

THE HOMEOWNER'S ONE NUMBER
* The note amount is the single figure the homeowner owes. It covers the whole renovation:
  what the contractor is paid, ROI Blocks' margin, and RKB's interest and two points for
  the first six months.
* Months 1 to 6: the homeowner is charged no interest. RKB still earns its interest and its
  two points for those months — they come out of ROI Blocks' margin, not from the homeowner.
* From month 7: interest accrues against the homeowner on the FULL note amount, at the rate
  in that deal's note. Usually 14%; 18% where Wes negotiated 18%. RKB collects whatever rate
  the note states.
* The homeowner makes no monthly payments. The note and the interest accrued from month 7
  are paid out of the sale proceeds; RKB is secured by its mortgage.

THE MARKUP, AND HOW IT IS SHARED
* The renovation is priced to the homeowner at a retail price. The builder cost is what the
  work actually costs. The difference — retail price less builder cost — is the markup.
  ROI Blocks holds the markup.
* First, off the top of the markup, RKB takes its two points and its interest for the
  first six months. Both are calculated on the BUILDER COST — not on the note amount and
  not on the retail price. This is how the homeowner's six interest-free months are paid
  for.
* What is left of the markup is then divided in two: half to RKB, half to ROI Blocks.
* ROI Blocks is paid half of its half at the start of the job and the other half at the
  end, after the house is sold.
* In order, then: markup, less two points, less six months' interest = the remainder;
  RKB gets 50% of the remainder; ROI Blocks gets 25% of it at the start and 25% after the
  sale. RKB's earnings on a deal are the points, the six months' interest, its half of the
  remainder, and the interest that accrues against the homeowner from month 7.
* The builder cost itself is what the contractor is paid, through the draws below.

DRAWS AND THE LAST 10%
* A construction draw pays 90% against completed work. The last 10% of a contractor's money
  is held back and paid at the sale. A hold-back is therefore not a bill being paid now and
  not a late payment.
* At the sale the contractor invoices its remaining 10%. That money goes to the contractor
  directly — from RKB, or from the title company — never to ROI Blocks. Ask the contractor
  for a final lien waiver when it is paid.
* Known exception, not settled: some suppliers are paid 100% directly, although the draw
  terms say 90%. Rakesh Sir is resolving this with Wes. Do not report it as an error and do
  not chase it.

AT THE SALE
* RKB is repaid from the proceeds: the note amount, plus the interest accrued from month 7.
* ROI Blocks is paid the second half of its share of the markup (its share having been
  worked out after the two points and the six months' interest came off the top).
* RKB pays the contractors and ROI Blocks itself after the sale, not through the title
  company, so the settlement statement carries one payoff to RKB. When the title company
  asks RKB for payoff figures, RKB supplies them.

IF THE DEAL FAILS
* RKB's interest and points are only as good as the sale. If the house does not sell, or a
  foreclosure brings in too little, RKB takes the loss and ROI Blocks does not receive its
  margin. Do not assume RKB can recover a shortfall from ROI Blocks.

PROTECTION ON EVERY LOAN
* 65% of ARV: the loan is not to exceed 65% of the after-repair value. Test it again when
  the title report arrives, because the report shows the other debts on the property and
  so RKB's true exposure.
* The title report is in RKB's hands before RKB funds. Whether the borrower has authority to
  borrow and to mortgage is for the title company to resolve, and RKB's closing instruction
  says so.
* A final lien waiver from each contractor (see above).

WHAT STAYS INSIDE RKB
* The homeowner deals with one number. How that number is divided between RKB, ROI Blocks
  and the contractor — including that the first six months' interest and the two points
  come out of ROI Blocks' margin — is internal. Do not put it in anything written to a
  homeowner, a realtor, a title company or any other outsider. Never write anything false
  about it either: if an outsider asks how the money is divided, do not answer in the
  draft — say that Rakesh Sir must decide the reply.

NOT YET SETTLED — do not state these as rules
* That the two points and the six months' interest are calculated on the builder cost is
  as Shivansh stated it on 2026-11-13; he is confirming it with Rakesh Sir. When you show
  this calculation, say that the basis is awaiting confirmation.
* What "75%" referred to in Rakesh Sir's statement of ROI Blocks' margin.
* Two Maryland questions Rakesh Sir is taking to counsel (Brian Gallagher): whether ROI
  Blocks records any mortgage or lien of its own for the work, and checking LP Remodeling's
  licence with the MHIC.
* An idea under consideration, not a practice: a first call with the homeowner and the
  realtor who brought the deal, and weekly or two-weekly progress reporting to them.
"""
