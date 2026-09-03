# Outlook access for Rakesh@mtreh.com — runbook

**Goal.** Read-only ingestion of the Inbox and Sent Items of `Rakesh@mtreh.com`,
with **no access to the other 35 mailboxes** in the tenant, no PowerShell, and no
long-lived secret stored anywhere.

**Method.** Delegated OAuth via device code flow. Rakesh signs in once; the token
carries only his authority.

---

## Why delegated, and why the earlier app-only plan was dropped

The first version of this runbook used **application permissions** (client
credentials). That path works, but it has one property that decided against it:
the application `Mail.Read` permission has **no per-mailbox form**. Microsoft's
consent screen says so plainly — *"Read mail in all mailboxes."* Granting it means
a client secret in a `.env` file that can read all 36 mailboxes in `mtreh.com`,
indefinitely, with no consent trail tied to a person. The fix was an Exchange
**Application Access Policy** to fence the app back to one mailbox — a real
control, but one we do not own and cannot see working, and it requires PowerShell.

**Delegated consent is scoped by construction.** Rakesh consents for his own
mailbox; the resulting token cannot reach anyone else's. There is no tenant-wide
grant to fence, so the fence — and the PowerShell — disappear.

The original objection to delegated auth was token expiry: *"a refresh token
expires and eventually requires a human."* That was wrong. Refresh tokens renew on
every use, so nightly ingestion keeps one alive indefinitely. See
[Token lifetime](#token-lifetime--what-actually-breaks-it) for the cases that do
break it.

### The three options, compared

| | Steps for Rakesh | Secret on disk | What is granted |
|---|---|---|---|
| App-only + Access Policy | Sign in, run PowerShell, consent | Yes, rotate ≤24 months | All 36 mailboxes, fenced to 1 |
| App-only, code-restricted | One consent click | Yes, rotate ≤24 months | **All 36 mailboxes, unfenced** |
| **Delegated (chosen)** | **One browser sign-in** | **None** | **His mailbox only** |

### Why device code flow specifically

Authorization-code flow needs a redirect URI and a local web server to catch the
callback — more to register, more to misconfigure. Username/password (ROPC)
cannot be used at all: it breaks under MFA, which any Global Admin should have.

Device code needs nothing but the app registration. We print a URL and an
eight-character code; Rakesh opens the URL on any device and signs in normally.
He never sees a terminal.

---

## What is needed, in total

Two values, and one sign-in. **No client secret.**

```
GRAPH_TENANT_ID=...      # Directory (tenant) ID
GRAPH_CLIENT_ID=...      # Application (client) ID
GRAPH_MAILBOX=Rakesh@mtreh.com
```

| # | Step | Who | Time |
|---|---|---|---|
| 1 | Register the app in Entra | Developer, or Rakesh if user registration is disabled | 3 min |
| 2 | Turn on **Allow public client flows** | Same | 30 sec |
| 3 | Add **delegated** `Mail.Read` + `offline_access` | Same | 1 min |
| 4 | Copy Tenant ID and Client ID into `.env` | Developer | 1 min |
| 5 | Run `outlook-auth`, Rakesh enters the code | **Rakesh** | 60 sec |
| 6 | Negative test — another mailbox must return 403 | Developer | automatic |

---

## Step 1 — register the app

1. Go to <https://entra.microsoft.com> and sign in.
2. **Check the tenant first.** Top-right account switcher must read `mtreh.com`.
   An admin account can belong to several directories and the portal picks a
   default. An app registered in the wrong tenant authenticates successfully and
   returns zero mailboxes — a failure that looks exactly like a permissions bug
   and wastes hours.
3. **Applications** → **App registrations** → **New registration**.
4. Fill in:
   - **Name:** `MangoTree Mail Ingestion`
   - **Supported account types:** *Accounts in this organizational directory only
     (mtreh.com only - Single tenant)*
   - **Redirect URI:** leave completely blank. Device code flow does not use one.
5. **Register**.

## Step 2 — allow public client flows

Left menu → **Authentication** → scroll to **Advanced settings** →
**Allow public client flows** → **Yes** → **Save**.

Device code flow will not start without this. The symptom is a device-flow
response with no `user_code` in it, and our CLI reports exactly that.

This is safe here precisely because there is no secret: a public client cannot
act on its own, only on behalf of a user who has interactively signed in.

## Step 3 — delegated permissions

Left menu → **API permissions** → **Add a permission** → **Microsoft Graph** →
**Delegated permissions** (*not* Application permissions).

Tick:
- `Mail.Read` — read the signed-in user's mail
- `offline_access` — issue a refresh token so the sign-in persists

Then **Add permissions**.

Notes:
- `Mail.Read`, not `Mail.ReadBasic`: we need bodies and attachments.
- `Mail.Read`, not `Mail.ReadWrite`: ingestion never writes. Draft-writing, if
  ever approved, is a separate permission and a separate decision.
- **Delegated**, not Application: this is the whole point. The Delegated version
  of `Mail.Read` is described as *"Read user mail"*; the Application version says
  *"Read mail in all mailboxes."* If the description mentions all mailboxes, the
  wrong tab is selected.
- **"Grant admin consent" is usually unnecessary.** Delegated `Mail.Read` is
  user-consentable, so Rakesh consents during sign-in. Click it only if the
  tenant has disabled user consent, which shows up as a consent error at step 5.

## Step 4 — copy the two values

App registration → **Overview**:

- **Directory (tenant) ID** → `GRAPH_TENANT_ID`
- **Application (client) ID** → `GRAPH_CLIENT_ID`

Both are non-secret identifiers — they are safe to paste into `.env` and to send
over chat. There is deliberately no third value.

## Step 5 — Rakesh signs in, once

```bash
python -m mangotree.cli outlook-auth
```

Prints something like:

```
Go to:  https://microsoft.com/devicelogin
Code:   K4RN-8QWZ
Waiting for Rakesh@mtreh.com to complete sign-in...
```

Rakesh opens that URL on any device, enters the code, signs in with his normal
password and MFA, and approves the consent screen — which reads *read your mail*,
not "all mailboxes."

The command verifies that the account which signed in is actually
`Rakesh@mtreh.com` and fails loudly otherwise. Signing in as the wrong person
would otherwise produce a perfectly working client pointed at a mailbox nobody
approved.

The token cache is written to `.secrets/graph_token_cache.json`, git-ignored and
owner-readable only. It is a bearer credential for a live mailbox: never commit
it, never paste it, never copy it to another machine.

## Step 6 — negative test

```bash
python -m mangotree.cli outlook-verify --other jp@mtreh.com
```

Reads Rakesh's inbox (must succeed) and another mailbox (must return `403`).
Config screens can lie; a live 403 cannot. Under delegated consent the denial is
structural, so this should pass with nothing else configured — it is run anyway,
because it turns "delegated auth is scoped by design" into a recorded observation
and would catch an app mistakenly granted application permissions as well.

---

## Token lifetime — what actually breaks it

Access tokens last about an hour and refresh silently. The refresh token renews on
every use, so nightly ingestion keeps it alive indefinitely.

It stops working in four cases:

1. Rakesh changes his Microsoft password
2. His MFA is reset or re-enrolled
3. Nothing runs for roughly 90 days
4. Someone revokes the app or his sessions in Entra

Recovery in all four is **re-running `outlook-auth` and re-entering a code — the
same 60 seconds.** The registration, config and code are untouched.

`mangotree status` reports token health so the warning arrives before ingestion
breaks rather than as a silent gap in the archive. A gap in a mail archive is not
something you notice by looking at it.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Device flow returns no `user_code` | Public client flows disabled | Step 2 |
| `AADSTS65001` consent required | Tenant disabled user consent | Rakesh clicks **Grant admin consent** once |
| `AADSTS7000218` | App treated as confidential client | Step 2 |
| Signs in fine, zero mailboxes | App registered in the wrong tenant | Re-register, check the switcher |
| `403` reading Rakesh's own mailbox | `Mail.Read` added as Application not Delegated | Step 3, correct tab |
| Silent refresh fails later | See token lifetime above | Re-run `outlook-auth` |

---

## What we will read

- **Only** `Rakesh@mtreh.com`, **only** Inbox and Sent Items (admin directive).
  Drafts, Deleted Items, Archive, Junk and Calendar are out of scope, enforced in
  `graph_client.ALLOWED_FOLDERS`.
- Read-only. Nothing is modified, moved, flagged or deleted.
- Deduplicated against the Gmail corpus on `Internet-Message-ID`, so a message
  reaching both providers is stored once with both provenance records.

## The Send-As problem this does not solve

Mail Rakesh sends from Gmail using the `Rakesh@mtreh.com` dropdown never touches
the Exchange mailbox — it exists only in **Gmail's** Sent folder. No Outlook
configuration can recover it. That is why both providers are ingested, and why
direction is attributed by the folder a message was found in rather than by its
`From` header. Neither mailbox alone is a complete record of what Rakesh sent.
