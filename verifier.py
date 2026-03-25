"""
Email verification logic:
  1. Syntax check
  2. MX record lookup
  3. SMTP handshake (RCPT TO)
  4. Disposable domain check
"""

import re
import smtplib
import socket
import dns.resolver

DISPOSABLE_DOMAINS = {
    "mailinator.com","guerrillamail.com","tempmail.com","throwaway.email",
    "yopmail.com","sharklasers.com","guerrillamailblock.com","grr.la",
    "guerrillamail.info","spam4.me","trashmail.com","dispostable.com",
    "maildrop.cc","spamgourmet.com","10minutemail.com","fakeinbox.com",
}

EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def check_syntax(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email.strip()))


def get_mx(domain: str) -> list[str]:
    try:
        records = dns.resolver.resolve(domain, "MX", lifetime=5)
        return sorted([str(r.exchange).rstrip(".") for r in records],
                      key=lambda x: x)
    except Exception:
        return []


def smtp_check(email: str, mx_host: str) -> tuple[bool, str]:
    """
    Attempt SMTP handshake with the MX server.
    Returns (accepted, reason).
    """
    try:
        with smtplib.SMTP(timeout=8) as smtp:
            smtp.connect(mx_host, 25)
            smtp.ehlo_or_helo_if_needed()
            smtp.mail("verify@mumbo-leads.com")
            code, msg = smtp.rcpt(email)
            smtp.quit()
            if code == 250:
                return True, "smtp_ok"
            elif code in (550, 551, 552, 553, 554):
                return False, f"smtp_rejected_{code}"
            else:
                return None, f"smtp_uncertain_{code}"
    except smtplib.SMTPConnectError:
        return None, "smtp_connect_error"
    except smtplib.SMTPServerDisconnected:
        return None, "smtp_disconnected"
    except socket.timeout:
        return None, "smtp_timeout"
    except Exception as e:
        return None, f"smtp_error"


def verify_email(email: str) -> dict:
    email = email.strip().lower()
    result = {
        "email":      email,
        "status":     "unknown",
        "reason":     "",
        "mx":         "",
    }

    # 1 — Syntax
    if not check_syntax(email):
        result.update({"status": "invalid", "reason": "bad_syntax"})
        return result

    domain = email.split("@")[1]

    # 2 — Disposable
    if domain in DISPOSABLE_DOMAINS:
        result.update({"status": "invalid", "reason": "disposable"})
        return result

    # 3 — MX record
    mx_hosts = get_mx(domain)
    if not mx_hosts:
        result.update({"status": "invalid", "reason": "no_mx_record"})
        return result

    result["mx"] = mx_hosts[0]

    # 4 — SMTP handshake (try up to 2 MX hosts)
    for mx in mx_hosts[:2]:
        accepted, reason = smtp_check(email, mx)
        if accepted is True:
            result.update({"status": "valid", "reason": reason})
            return result
        elif accepted is False:
            result.update({"status": "invalid", "reason": reason})
            return result
        # None = uncertain, try next MX

    # MX exists but SMTP couldn't confirm — mark as risky
    result.update({"status": "risky", "reason": "smtp_unverifiable"})
    return result
