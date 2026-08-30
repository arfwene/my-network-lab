#!/usr/bin/env python3
"""
설계 -> 교육생용 Xshell 세션 폴더 생성.

usage:
    python3 tools/gen-xshell.py --lab 1 [--user user01] [--out dist]
    python3 tools/gen-xshell.py --lab 1 --zip dist/my-network-lab-lab1.zip
    python3 tools/gen-xshell.py --lab 1 --zip -        # 표준출력으로 (콘솔이 쓴다)

`~/.ssh/config` 를 쓰지 않는 사람을 위한 두 번째 경로다. 만들어지는 것은
**폴더 하나**(my-network-lab)이고, 그 안에 노드 수만큼의 `.xsh` 세션이 들어간다.
Xshell 의 세션 폴더에 통째로 넣으면 세션 목록에 그대로 나타난다.

경유 방법 — Xshell 에는 OpenSSH 의 ProxyJump 가 없다. 대신 **프록시 종류
JUMPHOST** 가 같은 일을 한다. 프록시는 세션 파일이 아니라 Xshell 의 **프록시
목록**(전역)에 등록되고, 세션 파일은 그 이름만 적어 둔다 — `[CONNECTION:PROXY]`
의 `Proxy=`. 그래서 프록시 등록 한 번은 교육생이 직접 해야 하고, 그 절차를
같은 폴더의 「읽어보세요.txt」에 넣는다.

파일 형식은 Xshell 8 이 저장하는 그대로다. **UTF-16 LE + BOM, 줄바꿈 CRLF.**
다른 인코딩으로 쓰면 Xshell 이 세션 목록에 아예 띄우지 않는다.
"""
import argparse
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labdesign as L

FOLDER = "my-network-lab"
# Xshell [사용자 키 관리자]에 등록될 개인 키의 이름. 세션 파일이 이 이름을 가리킨다.
KEY_NAME = "my-network-lab"
# 점프 호스트 세션 파일. 프록시 INI 의 SESSION= 이 이 파일을 가리킨다.
JUMP_FILE = "0-점프호스트 (프록시용).xsh"

# Xshell 이 저장한 파일에서 그대로 가져온 값들. 건드리지 않는다 —
# 여기 있는 것을 빼면 Xshell 이 자기 기본값으로 채우므로 동작에는 문제가 없지만,
# CodePage(UTF-8)처럼 빠지면 랩의 한글 출력이 깨지는 것이 섞여 있다.
COMMON = {
    # `Description=Xshell session file` 은 사람이 읽는 설명이 아니라 **파일 형식**
    # 표시다. Xshell 이 저장한 파일에 그대로 들어 있으므로 손대지 않는다.
    "SessionInfo": [("Version", "8.1"), ("Description", "Xshell session file")],
    "CONNECTION:SSH": [
        ("AgentForwarding", "0"),
        ("Compression", "0"),
        ("ForwardX11", "0"),
        # 호스트 키는 세션 파일이 아니라 Xshell 의 [호스트 키 관리자]에 쌓인다.
        # 여기 박아 두면 랩을 다시 만들었을 때 옛 키가 세션에 고정돼 거부당한다.
        ("SaveHostKey", "0"),
        ("UseAuthAgent", "0"),
    ],
    "TERMINAL": [
        ("Type", "xterm"),
        ("CodePage", "65001"),       # UTF-8. 이게 없으면 한글 출력이 깨진다
        ("Rows", "24"), ("Cols", "80"),
        ("ScrollbackSize", "10240"),
        ("CJKAmbiAsWide", "0"),
    ],
    "CONNECTION:KEEPALIVE": [
        # 실습은 캡처를 걸어 두고 기다리는 시간이 길다. 끊기면 그 캡처가 날아간다.
        ("KeepAlive", "1"), ("KeepAliveInterval", "60"),
        ("SendKeepAlive", "1"), ("SendKeepAliveInterval", "60"),
    ],
    "ADVANCED": [("IPV", "0")],
}


def _session(host, port, user, proxy, desc, key):
    """한 세션의 섹션 목록. Xshell 이 쓰는 순서를 흉내낼 필요는 없다."""
    s = {k: list(v) for k, v in COMMON.items()}
    s["CONNECTION"] = [
        ("Protocol", "SSH"), ("Host", host), ("Port", str(port)),
        ("Description", desc),
        ("AutoReconnect", "0"), ("DefaultIcon", "1"), ("UseCustomIcon", "0"),
    ]
    s["CONNECTION:AUTHENTICATION"] = [
        ("UserName", user),
        # 이 랩은 어디서도 비밀번호로 로그인하지 않는다. 비워 두면 Xshell 이
        # 공개 키를 먼저 시도하고, 실패했을 때 이유가 그대로 보인다.
        ("Password", ""), ("Passphrase", ""),
        # Xshell 은 개인 키를 파일 경로가 아니라 **[사용자 키 관리자]에 등록된
        # 이름**으로 가리킨다. 비워 두면 접속할 때마다 키를 고르라고 묻는다 —
        # 세션이 14개라 14번 묻는다. 이름을 정해 두고, 그 이름으로 가져오라고
        # 「읽어보세요.txt」에서 안내한다.
        ("UserKey", key),
        # Xshell 이 저장하는 기본 목록 그대로다. 랩 노드의 sshd 가 비밀번호를
        # 아예 제공하지 않으므로 순서를 바꿀 이유가 없다.
        ("AuthMethodList", "00,11,20,30"),
        ("UseAuthProfile", "0"),
    ]
    s["CONNECTION:PROXY"] = [("Proxy", proxy), ("StartUp", "0")]
    return s


def _render(sections):
    """Xshell 8 이 읽는 바이트열. UTF-16 LE + BOM, CRLF."""
    out = []
    for name, items in sections.items():
        out.append(f"[{name}]")
        out += [f"{k}={v}" for k, v in items]
    return ("\r\n".join(out) + "\r\n").encode("utf-16-le")


def _bom(data):
    return b"\xff\xfe" + data


def build(lab_id, user=None, key=KEY_NAME):
    """{폴더 안 파일 이름: 바이트열} 을 만든다."""
    A = L.IPAM["access"]
    jump_ip = A["jump_host"]["office_ip"]
    jump_user = user or A["jump_host"]["user"]
    lab_user = A["lab_user"]
    proxy = f"lab{lab_id}-jump"

    files = {}
    # 점프 호스트 자신. 프록시의 [세션 파일] 인증에 이 파일을 지정한다.
    #   셸이 없는 계정이라 이것만 열면 바로 끊긴다 — 그게 정상이다.
    files[JUMP_FILE] = _bom(_render(_session(
        jump_ip, 22, jump_user, "",
        f"my-network-lab lab{lab_id} 점프 호스트", key)))

    for n in L.TOPO["nodes"]:
        name = n["name"]
        files[f"{name}.xsh"] = _bom(_render(_session(
            L.mgmt_ip(lab_id, name), 22, lab_user, proxy,
            f"lab{lab_id} {name} — {n.get('desc', '')}".strip(" —"), key)))

    files["install.ps1"] = _installer(lab_id, jump_ip, proxy, key)
    files["설치.bat"] = _launcher()
    files["읽어보세요.txt"] = _readme(lab_id, jump_ip, jump_user, lab_user, proxy, key)
    return files


# ---------------------------------------------------------------------------
#  프록시는 세션 파일이 아니라 **따로 있는 INI 파일**이다.
#
#      문서\NetSarang Computer\8\Common\Proxy\<이름>.ini
#      [SECTION]
#      TYPE=5            <- 5 = JUMPHOST
#      HOST=... PORT=22
#      SESSION=<점프 호스트 세션 파일의 **절대 경로**>
#      USERNAME= PASSWORD=   <- 인증을 SESSION 에 맡기므로 비어 있다
#
#  SESSION 이 절대 경로라 우리가 미리 만들어 둘 수 없다 — 계정 이름도 다르고
#  「문서」가 OneDrive 아래로 옮겨간 PC 도 있다. 그래서 파일을 넣는 대신
#  **그 자리에서 경로를 알아내 쓰는 스크립트**를 넣는다. 교육생이 20명이면
#  손으로 하는 프록시 등록도 20번이다.
# ---------------------------------------------------------------------------
def _installer(lab_id, jump_ip, proxy, key):
    ps = rf'''# my-network-lab · lab{lab_id} — Xshell 세션 설치
# 하는 일 두 가지뿐입니다.
#   ① 이 폴더의 세션 파일을 Xshell 세션 폴더로 복사
#   ② 점프 호스트 프록시({proxy}) 를 만들어 준다
$ErrorActionPreference = "Stop"

# 「문서」는 OneDrive 로 옮겨가 있을 수 있다. 레지스트리에 적힌 실제 위치를 묻는다.
$docs = [Environment]::GetFolderPath("MyDocuments")
$base = Join-Path $docs "NetSarang Computer\8"
if (-not (Test-Path $base)) {{
    Write-Host "Xshell 8 폴더를 찾지 못했습니다:" -ForegroundColor Red
    Write-Host "  $base"
    Write-Host "Xshell 8 을 한 번 실행한 뒤 다시 시도해 주세요."
    exit 1
}}

$src      = $PSScriptRoot
$sessions = Join-Path $base "Xshell\Sessions\{FOLDER}"
$proxydir = Join-Path $base "Common\Proxy"
New-Item -ItemType Directory -Force $sessions | Out-Null
New-Item -ItemType Directory -Force $proxydir | Out-Null

# ① 세션 복사. 이미 그 자리에서 돌리고 있으면 건너뛴다.
if ($src -ne $sessions) {{
    Copy-Item (Join-Path $src "*.xsh") $sessions -Force
    Copy-Item (Join-Path $src "읽어보세요.txt") $sessions -Force
    Copy-Item (Join-Path $src "install.ps1") $sessions -Force
    Copy-Item (Join-Path $src "설치.bat") $sessions -Force
    Write-Host "세션 복사: $sessions" -ForegroundColor Green
}} else {{
    Write-Host "세션은 이미 제자리에 있습니다." -ForegroundColor Green
}}

# ② 프록시 INI. SESSION 은 절대 경로여야 한다.
$jumpfile = Join-Path $sessions "{JUMP_FILE}"
if (-not (Test-Path $jumpfile)) {{
    Write-Host "점프 호스트 세션 파일이 없습니다: $jumpfile" -ForegroundColor Red
    exit 1
}}
$ini = @"
[SECTION]
JUMPHOST=
PASSWORD=
PORT=22
TYPE=5
HOST={jump_ip}
SESSION=$jumpfile
USERNAME=
"@
# Xshell 이 저장하는 그대로 UTF-16 LE + BOM 으로 쓴다.
[IO.File]::WriteAllText((Join-Path $proxydir "{proxy}.ini"), $ini, [Text.Encoding]::Unicode)
Write-Host "프록시 등록: {proxy}  ->  {jump_ip}:22" -ForegroundColor Green

# ③ 개인 키. Xshell 은 개인 키를 SECSH\UserKeys\<이름>.pri 로 둔다 — 경로도
#    이름도 우리가 아는 값이므로, 찾을 수 있으면 그냥 복사한다.
#    못 찾으면 조용히 넘어가지 않고 무엇을 해야 하는지 말한다.
$keydir  = Join-Path $base "SECSH\UserKeys"
$keyfile = Join-Path $keydir "{key}.pri"
New-Item -ItemType Directory -Force $keydir | Out-Null

if (Test-Path $keyfile) {{
    Write-Host "개인 키: 이미 등록돼 있습니다 ({key})" -ForegroundColor Green
}} else {{
    # 흔한 자리부터 본다. 마지막은 이 폴더 — 키 파일을 여기 같이 넣어 두면 잡힌다.
    $cands = @()
    foreach ($n in @("id_ed25519", "id_ecdsa", "id_rsa")) {{
        $cands += (Join-Path $HOME ".ssh\$n")
    }}
    $cands += @(Get-ChildItem $src -File -ErrorAction SilentlyContinue |
                Where-Object {{ $_.Name -match "\.(pri|key|pem)$" -or $_.Name -like "id_*" }} |
                ForEach-Object {{ $_.FullName }})

    $found = $null
    foreach ($c in $cands) {{
        if (Test-Path $c) {{
            $head = Get-Content $c -TotalCount 1 -ErrorAction SilentlyContinue
            # 실수로 공개 키(.pub)를 집지 않도록 개인 키인지 첫 줄로 확인한다.
            if ($head -match "-----BEGIN") {{ $found = $c; break }}
        }}
    }}

    if ($found) {{
        Copy-Item $found $keyfile -Force
        Write-Host "개인 키 등록: $found" -ForegroundColor Green
        Write-Host "             -> {key}" -ForegroundColor Green
    }} else {{
        Write-Host ""
        Write-Host "개인 키를 찾지 못했습니다. 이것만 직접 해 주세요." -ForegroundColor Yellow
        Write-Host "  방법 1 - 개인 키 파일을 이 이름으로 복사"
        Write-Host "           $keyfile"
        Write-Host "  방법 2 - Xshell [도구] > [사용자 키 관리자] > [가져오기] 로 넣고,"
        Write-Host "           [속성] 에서 이름을 {key} 로 바꾸기"
        $have = @(Get-ChildItem $keydir -Filter *.pri -ErrorAction SilentlyContinue |
                  ForEach-Object {{ $_.BaseName }})
        if ($have.Count -gt 0) {{
            Write-Host "  (지금 등록된 키: $($have -join ', '))" -ForegroundColor Yellow
        }}
        Write-Host "  공개 키는 웹 콘솔 [접속 키] 에도 등록돼 있어야 합니다." -ForegroundColor Yellow
    }}
}}

Write-Host ""
Write-Host "끝났습니다. Xshell 이 켜져 있으면 껐다 켜 주세요." -ForegroundColor Cyan
'''
    return b"\xef\xbb\xbf" + ps.replace("\n", "\r\n").encode("utf-8")


def _launcher():
    """PowerShell 스크립트는 두 번 눌러서 실행되지 않는다(실행 정책).

    이 한 줄짜리 배치가 그 자리에서만 정책을 우회해 띄운다 — PC 설정을
    바꾸지 않는다. 교육생에게 "정책을 푸세요" 라고 시키지 않기 위한 것이다.
    """
    # **내용은 ASCII 만 쓴다.** cmd 는 배치 파일을 현재 코드 페이지로 읽는다 —
    # 안에 한글이 있으면 PC 마다 다르게 깨져서 명령이 통째로 어긋난다.
    # 파일 **이름**의 한글은 상관없다(그건 파일 시스템이 다룬다).
    bat = ("@echo off\r\n"
           "chcp 65001 > nul\r\n"
           "powershell -NoProfile -ExecutionPolicy Bypass "
           "-File \"%~dp0install.ps1\"\r\n"
           "pause\r\n")
    return bat.encode("ascii")


def _readme(lab_id, jump_ip, jump_user, lab_user, proxy, key):
    # 메모장이 UTF-8 을 알아보도록 BOM 을 붙인다. 줄바꿈도 CRLF.
    t = f"""my-network-lab · lab{lab_id} Xshell 세션
============================================================

「설치.bat」 을 두 번 누르면 끝납니다.

랩 노드는 사무실에서 직접 보이지 않습니다. 운영 서버(점프 호스트)를 거쳐야
합니다. Xshell 에는 OpenSSH 의 ProxyJump 가 없고, 대신 **프록시 종류
JUMPHOST** 가 같은 일을 합니다. 설치 스크립트가 그 프록시까지 만들어 줍니다.


1. 설치.bat 을 두 번 누릅니다
------------------------------------------------------------
   세 가지를 합니다.

     · 세션 파일을 Xshell 세션 폴더로 복사
     · 점프 호스트 프록시({proxy}) 를 등록
     · 내 개인 키를 「{key}」 라는 이름으로 등록

   검은 창이 뜨고 **초록 글씨 세 줄**이 나오면 된 것입니다.
   노란 글씨가 나오면 그 줄이 시키는 것만 해 주세요 (아래 3번).
   Xshell 이 켜져 있었다면 **껐다 켜 주세요.** 세션 목록은 켤 때 읽습니다.

   [경고가 뜨면] Windows 가 "이 앱이 PC를 손상시킬 수 있습니다" 라고 물으면
   [추가 정보] > [실행] 입니다. 인터넷에서 받은 파일이라 뜨는 것입니다.


2. 접속
------------------------------------------------------------
   세션을 두 번 클릭합니다. 처음에는 호스트 키를 물어보는데
   [수락 및 저장] 을 누르면 다음부터 묻지 않습니다.

   랩을 다시 만들면 노드의 호스트 키가 바뀌어 경고가 한 번 더 뜹니다.
   **이 랩에서는 정상입니다** — [수락 및 저장] 을 다시 누르세요.
   실무에서 같은 경고를 보면 그때는 정상이 아닙니다. 이 랩은 장비를 통째로
   다시 만들기 때문에 바뀌는 것이고, 현장의 장비는 그럴 일이 없습니다.

   「0-점프호스트」 세션은 **직접 열지 마세요.** 프록시가 인증에 쓰는 파일입니다.
   열면 "This account is currently not available" 하고 바로 끊깁니다 —
   셸이 없는 계정이라 그게 정상입니다.


3. 개인 키를 못 찾았다고 할 때만
------------------------------------------------------------
   설치 스크립트가 흔한 자리를 먼저 봅니다.

     %USERPROFILE%\\.ssh\\id_ed25519 · id_ecdsa · id_rsa
     그리고 이 폴더 안의 키 파일

   여기 없으면 노란 글씨로 알려 줍니다. 둘 중 하나를 해 주세요.

     방법 1 - 개인 키 파일을 이 이름으로 복사한다 (제일 빠릅니다)

       ...\\NetSarang Computer\\8\\SECSH\\UserKeys\\{key}.pri

     방법 2 - [도구] > [사용자 키 관리자] > [가져오기] 로 넣고,
              [속성] 에서 이름을 「{key}」 로 바꾼다

   세션 파일들이 키를 **이름으로** 가리킵니다. 이름이 다르면 접속할 때마다
   키를 고르라고 묻습니다 — 세션이 여러 개라 그만큼 묻습니다.

   키 파일을 이 폴더에 같이 넣어 두고 설치.bat 을 다시 돌려도 됩니다.

   웹 콘솔 [접속 키] 에 등록한 그 키와 **짝이 맞는 개인 키**여야 합니다.
   Xshell 에서 키를 새로 만들었다면([도구] > [사용자 키 생성 마법사]),
   그 공개 키를 웹 콘솔 [접속 키] 에 등록하고 [지금 랩에 반영] 을 누르세요.


접속이 안 될 때
------------------------------------------------------------
 · 첫 홉에서 막힌다 (점프 호스트에서 Permission denied)
     운영 서버에 내 계정이 아직 없거나 키가 안 들어갔습니다.
     웹 콘솔 [접속 키] 화면의 안내를 먼저 보세요.
 · 노드에서만 막힌다
     웹 콘솔 [접속 키] > [지금 랩에 반영] 을 누르세요.
 · 비밀번호를 묻는다
     키 인증이 실패한 것입니다. 그 비밀번호는 존재하지 않습니다.
     3번의 키 등록을 확인하세요.
 · 키를 고르라고 묻는다
     키 이름이 「{key}」 가 아닙니다. [사용자 키 관리자] 에서 이름을 바꾸세요.
 · 세션 [속성] > [연결] > [프록시] 가 비어 있다
     프록시가 등록되지 않았습니다. 설치.bat 을 다시 돌리고 Xshell 을 껐다 켜세요.
 · 호스트 키 경고가 계속 거슬린다
     저장된 옛 키를 지워도 됩니다:
     ...\\NetSarang Computer\\8\\SECSH\\HostKeys\\key_<노드주소>_22.pub


손으로 할 때 (설치.bat 이 안 될 때)
------------------------------------------------------------
 ① 이 폴더를 통째로 Xshell 세션 폴더에 넣습니다.
    Xshell 메뉴 [파일] > [열기] 를 누르면 그 폴더가 열립니다. 보통 여기입니다:

      문서\\NetSarang Computer\\8\\Xshell\\Sessions\\

    OneDrive 를 쓰면 「문서」가 OneDrive 아래에 있습니다.

 ② 프록시를 만듭니다. 아무 노드 세션(예: pc1)을 오른쪽 클릭
    > [속성] > [연결] > [프록시] > [찾아보기] > [추가]

      이름   : {proxy}          <- 반드시 이대로 (세션들이 이 이름을 봅니다)
      종류   : JUMPHOST
      호스트 : {jump_ip}
      포트   : 22
      인증   : [세션 파일] 을 고르고 「{JUMP_FILE}」 를 지정

    파일로 직접 만들어도 됩니다. 아래 내용으로,
    ...\\NetSarang Computer\\8\\Common\\Proxy\\{proxy}.ini

      [SECTION]
      JUMPHOST=
      PASSWORD=
      PORT=22
      TYPE=5
      HOST={jump_ip}
      SESSION=<위 세션 폴더의 절대 경로>\\{JUMP_FILE}
      USERNAME=

    SESSION 은 **절대 경로**여야 합니다. 이것 때문에 파일을 미리 만들어 둘 수
    없어서 설치 스크립트가 있는 것입니다.


이 세션들은 배정된 랩(lab{lab_id}) 기준입니다. 랩이 바뀌면 다시 받으세요.

계정 — 점프 호스트 {jump_user} / 랩 노드 {lab_user}
"""
    return b"\xef\xbb\xbf" + t.replace("\n", "\r\n").encode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", type=int, default=1)
    ap.add_argument("--user", help="점프 계정 이름 (생략 시 site.yml 의 값)")
    ap.add_argument("--key", default=KEY_NAME,
                    help=f"Xshell 사용자 키 이름 (기본 {KEY_NAME})")
    ap.add_argument("--out", default="dist", help="여기 아래에 폴더를 만든다")
    ap.add_argument("--zip", dest="zip_to",
                    help="폴더 대신 zip 하나로. '-' 면 표준출력")
    a = ap.parse_args()

    files = build(a.lab, a.user, a.key)

    if a.zip_to:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(f"{FOLDER}/{name}", data)
        blob = buf.getvalue()
        if a.zip_to == "-":
            sys.stdout.buffer.write(blob)
        else:
            Path(a.zip_to).parent.mkdir(parents=True, exist_ok=True)
            Path(a.zip_to).write_bytes(blob)
            print(f"generated {a.zip_to}  ({len(files)} files)", file=sys.stderr)
        return

    d = Path(a.out) / FOLDER
    d.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (d / name).write_bytes(data)
    print(f"generated {d}/  ({len(files)} files)", file=sys.stderr)


if __name__ == "__main__":
    main()
