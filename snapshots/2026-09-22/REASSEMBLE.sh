#!/usr/bin/env bash
# 拼回完整压缩包。分片名 part_000000..part_NNNNNN 是顺序切块，按名排序 cat 即可。
# 用法:  cd snapshots/2026-09-22 && bash REASSEMBLE.sh
set -euo pipefail
cd "$(dirname "$0")"

reassemble() {
  local prefix="$1" out="$2" expected="$3"
  local parts="$prefix/part_"*
  local n
  n=$(ls $parts | wc -l)
  echo "==> $out  ($n 块, 期望 ${expected}B)"
  # 按名字数字序拼回
  ls $parts | sort | xargs cat > "$out"
  local actual
  actual=$(stat -c%s "$out")
  ls -lh "$out"
  if [ "$actual" = "$expected" ]; then echo "    OK: 大小匹配 ($actual)"; else echo "    !! 大小不符: 实际 $actual 期望 $expected"; fi
  tar -tzf "$out" >/dev/null && echo "    OK: gzip/tar 校验通过" || echo "    !! 校验失败（块可能有缺失）"
}

reassemble "Bg-harness.tar.gz"     "Bg-harness.tar.gz"     252413609
reassemble "Boogu-Image-code.tar.gz" "Boogu-Image-code.tar.gz" 70695785
