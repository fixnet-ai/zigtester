#!/usr/bin/env bash
# create-localhost-cert.sh — 生成 localhost 自签名开发证书 + 加入 macOS 系统信任。
#
# 用途：新机器恢复 fixnet 开发环境时，重建与现有生态**完全一致**的 localhost 证书
# （EC P-256 / PKCS#8 / CN=localhost / SAN 含 DNS:localhost + DNS:localhost.google.com
#  + IP:127.0.0.1），并加入系统 Keychain 信任，使 Apple curl / Safari / Chrome 直接信任
#  https://localhost。
#
# 用法：
#   ./create-localhost-cert.sh              # 生成 + 加入系统信任（已存在则跳过生成）
#   ./create-localhost-cert.sh --force      # 覆盖重生成（含信任）
#   ./create-localhost-cert.sh --no-trust   # 仅生成，不碰系统 Keychain（免 sudo）
#   ./create-localhost-cert.sh --force --no-trust   # 覆盖重生成且不信任
#
# 说明：
#   - 证书有效期默认 10 年（LOCALHOST_CERT_DAYS 可覆盖），避免开发证书频繁过期。
#   - 加入系统信任需要 sudo（security add-trusted-cert）；sudo 失败时打印手动命令，不判整体失败。
#   - Homebrew curl（LibreSSL）不读系统 Keychain，需另配 ~/.curlrc（见 README / findings §11 C.6）。

set -euo pipefail

CERT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRT="$CERT_DIR/localhost.crt"
KEY="$CERT_DIR/localhost.key"
DAYS="${LOCALHOST_CERT_DAYS:-3650}"

FORCE=0
NO_TRUST=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --no-trust) NO_TRUST=1 ;;
    --help|-h)
      echo "用法: $0 [--force] [--no-trust]"
      echo "  --force     覆盖已存在的证书重生成"
      echo "  --no-trust  仅生成证书，不加入系统 Keychain（免 sudo）"
      exit 0 ;;
    *)
      echo "未知参数: $arg（--help 查看用法）" >&2
      exit 1 ;;
  esac
done

# 依赖检查：openssl 必须可用（Homebrew openssl 3.x；macOS 自带 LibreSSL 的 -addext 需 3.1+）
if ! command -v openssl >/dev/null 2>&1; then
  echo "错误：未找到 openssl，请先安装（brew install openssl）" >&2
  exit 1
fi

# ── 1. 生成证书（幂等：已存在且非 --force 则跳过）──────────────────────────
if [[ -f "$CRT" && -f "$KEY" && "$FORCE" -ne 1 ]]; then
  echo "[cert] 证书已存在，跳过生成（--force 覆盖）：$CRT"
else
  echo "[cert] 生成 localhost 自签名证书（EC P-256 / PKCS#8 / ${DAYS} 天）..."
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -keyout "$KEY" -out "$CRT" -days "$DAYS" -nodes \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:localhost.google.com,IP:127.0.0.1"
  chmod 600 "$KEY"
  chmod 644 "$CRT"
  echo "[cert] 已生成：$CRT / $KEY"
fi

# ── 2. 自检：打印 SAN 确认与生态一致 ──────────────────────────────────────
echo "[cert] SAN 自检："
openssl x509 -in "$CRT" -noout -subject -ext subjectAltName

# ── 3. 加入系统 Keychain 信任 ─────────────────────────────────────────────
if [[ "$NO_TRUST" -eq 1 ]]; then
  echo "[cert] 跳过系统信任（--no-trust）"
elif [[ "$(uname)" != "Darwin" ]]; then
  echo "[cert] 非 macOS，跳过系统 Keychain 信任"
else
  echo "[cert] 加入系统 Keychain 信任（sudo 可能需要密码）..."
  if sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "$CRT"; then
    echo "[cert] 已信任：Apple curl / Safari / Chrome 可直接访问 https://localhost"
    echo "[cert] 提示：Homebrew curl（LibreSSL）不读 Keychain，需另配 ~/.curlrc（见 README）"
  else
    echo "[cert] 信任失败（sudo 被拒或 security 不存在）。可手动执行：" >&2
    echo "  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain $CRT" >&2
  fi
fi

echo "[cert] 完成"
