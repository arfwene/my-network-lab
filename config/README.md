# config/

| 파일 | git | 용도 |
|---|---|---|
| `site.yml` | ✅ 커밋 | 기본값. 공개해도 안전한 문서 전용 대역만 들어 있다. |
| `site.local.yml` | ❌ 제외 | **실제 환경 값.** `site.yml` 위에 깊은 병합된다. |
| `site.local.yml.example` | ✅ 커밋 | 위 파일의 작성 예시 |
| `../var/runtime.yml` | ❌ 제외 | 웹 콘솔 `[관리자 → 연결 설정]` 이 저장한 Proxmox 접속 정보. **손으로 고치지 말 것** (다음 저장 때 덮어쓴다) |

병합 순서는 **`site.yml` → `site.local.yml` → `var/runtime.yml`** 이고 뒤가 이긴다.
Proxmox 접속 정보는 웹에서 입력하는 쪽을 권장한다 — 그래야 저장 즉시 점검까지 함께 돈다.
파일로 관리하고 싶으면 `site.local.yml` 에 쓰고 콘솔에서 저장하지 않으면 된다.

> 웹 콘솔 **계정·API 토큰은 여기 없다.** `var/console.db` (SQLite) 에 있고 `tools/console-user.py` 로 관리한다.
> 예전 `config/users.yml` 은 콘솔 최초 기동 시 자동으로 DB 로 이관되고 `.migrated` 로 이름이 바뀐다.
> Proxmox **API 토큰**도 같은 DB 에 있다. 파일로는 내보내지 않는다.

```bash
cp config/site.local.yml.example config/site.local.yml
$EDITOR config/site.local.yml
python3 tools/validate-site.py        # 대역 충돌 · 공개 안전성 검사
```

**왜 이렇게 나누는가** — 저장소를 공개할 때 사내 IP 대역·도메인이 문서와 생성물에 박혀 나가는 사고를 구조적으로 막기 위해서다.
기본값만으로도 랩이 완전히 동작하므로, 처음 받은 사람은 아무것도 고치지 않고 바로 띄울 수 있다.
