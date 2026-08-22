"""
SSH 공개키 검사.

교육생이 직접 붙여넣는 값이라 그대로 믿을 수 없다. 이 값은 결국 랩 노드의
`authorized_keys` 로 들어가므로, 형식이 아니라 **무엇이 될 수 있는가**를 본다.

막는 것
  · 개인키를 잘못 붙여넣는 것 (가장 흔한 사고 — 붙이는 순간 유출이다)
  · authorized_keys 앞자리 옵션 (command=, from=, environment= ...)
    → 남의 키에 옵션을 얹어 강제 명령을 심을 수 있다
  · 줄바꿈으로 여러 줄을 밀어넣는 것 (한 번에 한 키만 받는다)

받는 것은 `<타입> <base64> [주석]` 한 줄뿐이다.
"""
import base64
import binascii
import hashlib
import re

# 이 랩에서 허용하는 키 타입. rsa 는 길이가 짧으면 위험해서 별도로 확인한다.
ALLOWED = {
    "ssh-ed25519": 256,
    "ssh-rsa": 2048,
    "ecdsa-sha2-nistp256": 256,
    "ecdsa-sha2-nistp384": 384,
    "ecdsa-sha2-nistp521": 521,
    "sk-ssh-ed25519@openssh.com": 256,
    "sk-ecdsa-sha2-nistp256@openssh.com": 256,
}
MAX_LEN = 1024                       # 정상 키는 길어야 ~750자
COMMENT_OK = re.compile(r"^[\w.@/+=:\- ]*$")


class Invalid(ValueError):
    """사람에게 그대로 보여줄 수 있는 거절 사유."""


def parse(raw):
    """한 줄짜리 공개키를 (타입, base64, 주석) 으로. 문제가 있으면 Invalid."""
    if raw is None:
        raise Invalid("공개키를 입력할 것")
    text = raw.strip()
    if not text:
        raise Invalid("공개키를 입력할 것")

    if "PRIVATE KEY" in text.upper():
        raise Invalid("개인키를 붙여넣었다. 절대 공유하지 말 것 — "
                      "`.pub` 로 끝나는 **공개키** 파일의 내용을 넣어야 한다 "
                      "(예: ~/.ssh/id_ed25519.pub). 이미 붙여넣었다면 그 키는 새로 만들 것")

    if len(text.splitlines()) > 1:
        raise Invalid("한 번에 키 하나만 넣는다. 줄바꿈 없이 한 줄로 붙여넣을 것")
    if len(text) > MAX_LEN:
        raise Invalid(f"너무 길다 ({len(text)}자). 공개키 한 줄만 넣을 것")

    parts = text.split()
    if len(parts) < 2:
        raise Invalid("형식이 아니다. `<타입> <키> [주석]` 한 줄이어야 한다 "
                      "(예: ssh-ed25519 AAAAC3Nza... me@laptop)")

    ktype = parts[0]
    if ktype not in ALLOWED:
        # authorized_keys 는 앞자리에 옵션을 둘 수 있다. 그걸 넣었다는 뜻이다.
        if "=" in ktype or ktype.startswith(("command", "from", "no-", "permitopen",
                                             "environment", "restrict", "tunnel")):
            raise Invalid("옵션이 붙어 있다. 옵션 없이 `<타입> <키> [주석]` 만 넣을 것")
        raise Invalid(f"지원하지 않는 키 타입: {ktype[:40]} "
                      f"(쓸 수 있는 것: {', '.join(sorted(ALLOWED))})")

    blob = parts[1]
    try:
        data = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError):
        raise Invalid("키 부분이 base64 가 아니다. 복사할 때 잘리지 않았는지 확인할 것") from None
    if len(data) < 32:
        raise Invalid("키가 너무 짧다. 파일 내용이 다 복사됐는지 확인할 것")

    # 앞부분에 타입 이름이 다시 들어 있다. 여기가 어긋나면 조작됐거나 깨진 것이다.
    try:
        n = int.from_bytes(data[:4], "big")
        if data[4:4 + n].decode("ascii") != ktype:
            raise Invalid("키 내용이 타입과 맞지 않는다. 다시 복사할 것")
    except (UnicodeDecodeError, IndexError):
        raise Invalid("키 내용을 읽을 수 없다. 다시 복사할 것") from None

    if ktype == "ssh-rsa" and len(data) < 279:      # 대략 2048bit 미만
        raise Invalid("RSA 키가 2048bit 미만이다. ssh-ed25519 를 권장한다 "
                      "(`ssh-keygen -t ed25519`)")

    comment = " ".join(parts[2:])[:80]
    if not COMMENT_OK.match(comment):
        comment = ""                    # 주석은 버려도 그만이다. 이상하면 조용히 없앤다
    return ktype, blob, comment


def normalize(raw):
    """저장할 형태로. 주석까지 포함한 한 줄."""
    ktype, blob, comment = parse(raw)
    return f"{ktype} {blob}" + (f" {comment}" if comment else "")


def fingerprint(raw):
    """`SHA256:...` — 화면에 보여줄 지문. 키 전체를 띄우는 것보다 확인하기 쉽다."""
    _, blob, _ = parse(raw)
    digest = hashlib.sha256(base64.b64decode(blob)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def describe(raw):
    """화면용 요약: (타입, 지문, 주석). 잘못된 값이면 None."""
    try:
        ktype, _, comment = parse(raw)
        return {"type": ktype, "fingerprint": fingerprint(raw), "comment": comment}
    except Invalid:
        return None
