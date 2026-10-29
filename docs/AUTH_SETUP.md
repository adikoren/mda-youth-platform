# Password reset and Google sign-in: setup

Secrets live on the server in `/etc/mda/env` and on your Mac in `~/mda-local-data/` (outside Git). Never paste them into a chat or a commit.

## 1. Email — password reset

Live reset emails use **Mailjet** (HTTPS API, free tier). The Gmail API route below stays available as an alternative. SMTP is not used: the Droplet cannot reach SMTP ports.

Variables:

| Name | Value |
|---|---|
| `MDA_EMAIL_MODE` | `mailjet` for live use. `gmail` for the Gmail API route. `capture` for local development (writes files, sends nothing). `off` sends nothing. |
| `MDA_MAILJET_API_KEY` | API key from Mailjet (Account settings → REST API → API key management). |
| `MDA_MAILJET_API_SECRET` | The matching secret key. |
| `MDA_GMAIL_CLIENT_ID` | OAuth client ID (Desktop app) from Google Cloud. Gmail route only. |
| `MDA_GMAIL_CLIENT_SECRET` | OAuth client secret of the same client. |
| `MDA_GMAIL_REFRESH_TOKEN` | Refresh token with the `gmail.send` scope, from the helper script below. |
| `MDA_EMAIL_FROM_ADDRESS` | The dedicated Gmail address (the account that gave the refresh token). |
| `MDA_EMAIL_FROM_NAME` | Defaults to `נוער מדא`. |
| `MDA_PUBLIC_BASE_URL` | The public HTTPS address of the app, currently `https://188-166-161-195.sslip.io`. Reset links are built from this only. |

### Mailjet setup (live route)

1. In Mailjet, go to **Account settings → Sender addresses & domains** and confirm the sender `mda101youth@gmail.com` from the link Mailjet emails to it. Mailjet refuses messages from unconfirmed senders.
2. Go to **Account settings → REST API → API key management**, copy the API key and secret key.
3. On the server, put `MDA_EMAIL_MODE=mailjet`, `MDA_MAILJET_API_KEY`, `MDA_MAILJET_API_SECRET` and `MDA_EMAIL_FROM_ADDRESS=mda101youth@gmail.com` into `/etc/mda/env`, then run `systemctl restart mda-api`.

Mailjet's answer "success" means it accepted the message, not that it reached the inbox.

### Gmail API route (alternative)

### Steps in Google Cloud Console

1. Sign in with the dedicated Gmail account. Create a project (for example `mda-youth`).
2. **APIs & Services → Library**: enable the **Gmail API**.
3. **APIs & Services → OAuth consent screen**: user type **External**, app name `נוער מדא`, support and developer e-mail = the Gmail address. Under **Scopes**, add `https://www.googleapis.com/auth/gmail.send` only.
4. Still on the consent screen, press **Publish app** (move to "In production"). While the app stays in "Testing", Google expires refresh tokens after 7 days and reset emails stop. Google may show an "unverified app" warning on the consent page; that is expected for a send-only app used by its owner.
5. **Credentials → Create credentials → OAuth client ID**, type **Desktop app**. Keep the client ID and secret for step 6.

### Get the refresh token (on your Mac)

6. From the repository folder run `python3 scripts/gmail-refresh-token.py`. Paste the client ID, then the client secret (hidden). Sign in with the Gmail account in the browser and allow sending.
7. The script writes `~/mda-local-data/gmail-oauth.env` (permissions 600) and prints only that path. Do not open it in a shared screen or paste it into chat.

### Put the values on the server (once)

8. Open a session on the Droplet: `ssh root@188.166.161.195`.
9. Edit the environment file: `nano /etc/mda/env`. Add or change these lines, copying the values from `~/mda-local-data/gmail-oauth.env`:

```
MDA_EMAIL_MODE=gmail
MDA_GMAIL_CLIENT_ID=...
MDA_GMAIL_CLIENT_SECRET=...
MDA_GMAIL_REFRESH_TOKEN=...
MDA_EMAIL_FROM_ADDRESS=the-gmail-address@gmail.com
MDA_EMAIL_FROM_NAME=נוער מדא
MDA_PUBLIC_BASE_URL=https://188-166-161-195.sslip.io
```

10. Restart the API: `systemctl restart mda-api`.

### Test with one real message

1. Open `https://188-166-161-195.sslip.io/login`, choose "שכחתי סיסמה", and enter your own registered address.
2. The screen shows the neutral message in every case.
3. The API answers "accepted" when Gmail queues the message. That is not delivery. Check your inbox and spam folder. If nothing arrives, check the API log on the server: `journalctl -u mda-api -n 50 --no-pager` (it records the reason, never a secret).

Note: "accepted" means Gmail queued the message, not that it reached the inbox.

## 2. Google sign-in

Variables: `MDA_GOOGLE_CLIENT_ID`, `MDA_GOOGLE_CLIENT_SECRET`. The button appears only when both are set.

Steps in Google Cloud Console:

1. Create (or choose) a project, then open **APIs & Services → OAuth consent screen**. Choose External, add the app name, and add your address as a test user while the app is in testing.
2. Under **Scopes**, add only `openid`, `email` and `profile`.
3. Open **Credentials → Create credentials → OAuth client ID**, type **Web application**.
4. Add these **Authorized redirect URIs** (one per environment):
   - Local: `http://localhost:5173/api/auth/google/callback`
   - Hosted: `https://188-166-161-195.sslip.io/api/auth/google/callback`
5. Copy the client ID and client secret into `/etc/mda/env` (or `secrets.env` locally).

Linking an existing account: sign in with email and password, open **החשבון שלי** (`/profile`), enter the password again, then press "קישור Google". The Google address must match the account's email. Signing in with Google alone never links an account by email.

## 3. What is and isn't automated

Automated tests use the capture folder and mocked Google and Gmail responses. Nothing is sent to real people during testing. The real Gmail send, the real Google token exchange, and the real consent flow are checked by hand, as above.
