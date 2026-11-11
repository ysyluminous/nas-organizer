#!/usr/bin/env bash
# 把项目内的 10 个 WorkBuddy Skill（1 路由 + 9 专项）安装到用户级目录。
# 用法：
#   bash scripts/install-skills.sh                # 安装到 ~/.workbuddy/skills
#   bash scripts/install-skills.sh /custom/path   # 安装到指定目录
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)/skills"
DEST_DIR="${1:-${HOME}/.workbuddy/skills}"

if [ ! -d "${SRC_DIR}" ]; then
  echo "错误：找不到 Skill 源目录 ${SRC_DIR}" >&2
  exit 1
fi

mkdir -p "${DEST_DIR}"
installed=0
for skill_file in "${SRC_DIR}"/*/SKILL.md; do
  [ -f "${skill_file}" ] || continue
  skill_name="$(basename "$(dirname "${skill_file}")")"
  mkdir -p "${DEST_DIR}/${skill_name}"
  cp "${skill_file}" "${DEST_DIR}/${skill_name}/SKILL.md"
  echo "  installed: ${skill_name}"
  installed=$((installed + 1))
done

echo "完成：${installed} 个 Skill 已安装到 ${DEST_DIR}"
echo "提示：如当前会话未识别，重启 WorkBuddy 或刷新 Skill 列表。"
