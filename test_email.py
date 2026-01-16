docker run --rm -i --network orion_net python:3.12-alpine python - <<'PY'
import smtplib, ssl, sys
from email.message import EmailMessage

SMTP_HOST="smtp.purelymail.com"
SMTP_PORT=587
USER="documents@alpwolf.at"
PASS="5&CRn$d$1pW80*FR09bo^7c0pvAv!Nn%^5A14fy0n%"
TO="accountmail@gmx.at"

print("Connecting…", flush=True)

try:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.set_debuglevel(1)
        s.ehlo()
        s.starttls(context=ssl.create_default_context())
        s.ehlo()
        print("Logging in…", flush=True)
        s.login(USER, PASS)
        print("Login OK", flush=True)

        msg=EmailMessage()
        msg["Subject"]="Purelymail SMTP auth test"
        msg["From"]=USER
        msg["To"]=TO
        msg.set_content("If you got this, SMTP auth works.")

        s.send_message(msg)
        print("Sent OK", flush=True)

except Exception as e:
    print("ERROR:", repr(e), file=sys.stderr)
    sys.exit(1)
PY
