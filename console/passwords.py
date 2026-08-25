"""비밀번호 해싱과 정책. db 와 auth 양쪽에서 쓰므로 별도 모듈로 뺀다 (순환 import 방지)."""
import hashlib
import hmac
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L

ITER = 200_000
_POLICY = (L.SITE.get("console") or {}).get("password_policy") or {}
MIN_LEN = int(_POLICY.get("min_length", 8))
REQUIRE_SPECIAL = bool(_POLICY.get("require_special", True))
SPECIAL = _POLICY.get("special_chars") or "!@#$%^&*()-_=+[]{};:,.<>/?\\|~`'\""


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), ITER)
    return f"pbkdf2${ITER}${salt}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, want = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def policy_text():
    parts = [f"{MIN_LEN}자 이상"]
    if REQUIRE_SPECIAL:
        parts.append("특수문자 1개 이상")
    return ", ".join(parts)


def validate(pw: str, username: str = "") -> list[str]:
    """정책 위반 목록. 비어 있으면 통과."""
    errs = []
    if len(pw or "") < MIN_LEN:
        errs.append(f"{MIN_LEN}자 이상이어야 합니다 (현재 {len(pw or '')}자)")
    if REQUIRE_SPECIAL and not any(c in SPECIAL for c in (pw or "")):
        errs.append("특수문자를 1개 이상 포함해야 합니다")
    if username and pw and username.lower() in pw.lower():
        errs.append("계정 이름을 비밀번호에 포함할 수 없습니다")
    return errs


def generate(n=14):
    """정책을 통과하는 임시 비밀번호를 만든다."""
    pool = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(50):
        pw = "".join(secrets.choice(pool) for _ in range(n - 2))
        pw += secrets.choice("!@#$%^&*-_=+")
        pw += secrets.choice(pool)
        if not validate(pw):
            return pw
    return "Lab!" + secrets.token_urlsafe(10)
