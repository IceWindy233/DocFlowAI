#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "尚未初始化 Git 仓库。"
  exit 1
fi

blocked_paths='(^|/)(\.env|data|backend/data|corpus/letters|corpus/requests)(/|$)|(^|/)面试参考\.md$'
if git ls-files | rg -n "$blocked_paths"; then
  echo "发现不应跟踪的本地数据路径。"
  exit 1
fi

secret_patterns='sk-[A-Za-z0-9._-]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|/Users/[^/]+/'
if git grep -n -E "$secret_patterns" -- . ':!scripts/check-public-repo.sh'; then
  echo "发现疑似密钥、私钥或本机绝对路径。"
  exit 1
fi

oversized=0
while IFS= read -r file; do
  [[ -f "$file" ]] || continue
  size_kb=$(du -k "$file" | awk '{print $1}')
  if (( size_kb > 20480 )); then
    echo "跟踪文件超过 20 MiB：$file"
    oversized=1
  fi
done < <(git ls-files)

if (( oversized != 0 )); then
  exit 1
fi

echo "公开仓库检查通过。"
