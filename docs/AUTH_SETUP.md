# Password reset and Google sign-in: setup

Secrets live in `~/mda-local-data/secrets.env` (outside Git). Never paste them into a chat or a commit.

## 1. Email (Brevo) — password reset

Variables:

| Name | Value |
|---|---|
| `MDA_EMAIL_MODE` | `brevo` for live use. `capture` for local development (writes files, sends nothing). `off` sends nothing. |
| `BREVO_API_KEY` | API key from Brevo (Settings → SMTP & API → API keys). |
| `MDA_EMAIL_FROM_ADDRESS` | The verified sender address (see below). |
| `MDA_EMAIL_FROM_NAME` | Defaults to `נוער מדא`. |
| `MDA_PUBLIC_BASE_URL` | The trusted address of the app, e.g. `http://localhost:5173` or `https://your-host`. Reset links are built from this only. |

Steps:

1. Create a Brevo account at brevo.com.
2. **Verify a sender.** Go to Senders & IP, add the sender address, and confirm it from the email Brevo sends you. Brevo sends from an authenticated sender only. Gmail addresses cannot be used as the authenticated sender; use an address on a domain you control, or Brevo's own sender setup.
3. **Authenticate the domain (recommended).** Add the SPF and DKIM records Brevo shows for your domain at your DNS provider. Without them, mail may go to spam.
4. Create an API key and put it in `secrets.env` as `BREVO_API_KEY=...`.
5. Set `MDA_EMAIL_MODE=brevo` and the other variables for the live environment.

Test with one real message to your own address:

1. Set the variables above, restart the API.
2. Open the login page, choose "שכחתי סיסמה", enter your own address.
3. The screen shows the neutral message. Open the email. If it doesn't arrive, check Brevo's transactional log for the message status.

Note: Brevo's "accepted" means queued, not delivered. Check the log before treating a message as delivered.

## 2. Google sign-in

Variables: `MDA_GOOGLE_CLIENT_ID`, `MDA_GOOGLE_CLIENT_SECRET`. The button appears only when both are set.

Steps in Google Cloud Console:

1. Create (or choose) a project, then open **APIs & Services → OAuth consent screen**. Choose External, add the app name, and add your address as a test user while the app is in testing.
2. Under **Scopes**, add only `openid`, `email` and `profile`.
3. Open **Credentials → Create credentials → OAuth client ID**, type **Web application**.
4. Add these **Authorized redirect URIs** (one per environment):
   - Local: `http://localhost:5173/api/auth/google/callback`
   - Hosted: `https://<your-host>/api/auth/google/callback`
5. Copy the client ID and client secret into `secrets.env`.

Linking an existing account: sign in with email and password, open **החשבון שלי** (`/profile`), enter the password again, then press "קישור Google". The Google address must match the account's email. Signing in with Google alone never links an account by email.

## 3. What is and isn't automated

Automated tests use the capture folder and mocked Google responses. Nothing is sent to real people during testing. The real Google token exchange and the real Brevo send are the two steps that need your accounts and must be checked by hand, as above.
