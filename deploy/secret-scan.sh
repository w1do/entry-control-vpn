#!/usr/bin/env bash
# =============================================================================
# Базовый secret-scan рабочего дерева (без gitleaks и без git).
#
# Ищет в текстовых файлах репозитория:
#   * приватные ключи (реальный PEM-блок: BEGIN + END),
#   * AWS/GitHub/OpenAI/Slack-токены,
#   * реальные значения ADMIN, PASSWORD, SECRET_KEY, ENCRYPTION_KEY,
#     METRICS_TOKEN, POSTGRES_PASSWORD (плейсхолдеры игнорируются),
#   * отсутствие .env / !.env.example в .gitignore.
#
# deploy/.env НЕ читается и не сканируется (исключён по имени).
# Выводится только файл:строка и правило — значения в лог не попадают.
#
# Использование:
#   bash deploy/secret-scan.sh
#   make secret-scan
# Код возврата: 0 — чисто, 1 — найдены секреты, 2 — ошибка запуска.
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || { echo "secret-scan: cannot locate repo root" >&2; exit 2; }
cd "$ROOT" || { echo "secret-scan: cannot cd to $ROOT" >&2; exit 2; }

# Каталоги, которые не сканируются (зависимости, сгенерированное, локальные секреты).
EXCLUDE_DIRS=(
  .git .venv .tools .tmp .idea .vscode
  node_modules __pycache__ .pytest_cache .ruff_cache .mypy_cache .vite
  coverage htmlcov dist build artifacts agent/bin agent/dist
)
exclude_dir_args=()
for d in "${EXCLUDE_DIRS[@]}"; do exclude_dir_args+=("--exclude-dir=$d"); done

# Файлы, которые не сканируются: deploy/.env, БД, логи, бинарники.
EXCLUDE_FILES=(
  .env "*.db" "*.sqlite" "*.sqlite3" "*.log"
  "*.png" "*.jpg" "*.jpeg" "*.gif" "*.ico" "*.svg" "*.webp"
  "*.woff" "*.woff2" "*.ttf" "*.zip" "*.gz" "*.tar"
)
exclude_file_args=()
for f in "${EXCLUDE_FILES[@]}"; do exclude_file_args+=("--exclude=$f"); done

GREP=(grep -rInE "${exclude_dir_args[@]}" "${exclude_file_args[@]}")

findings=0
report_finding() { # $1 = rule, $2 = path, $3 = line
  findings=$((findings + 1))
  printf 'FINDING [%s] %s:%s\n' "$1" "$2" "$3"
}

# -----------------------------------------------------------------------------
# 1. Паттерны, не имеющие безопасных вариантов в репозитории.
# -----------------------------------------------------------------------------
scan_regex() { # $1 = rule, $2 = ERE
  local rule="$1" rx="$2" hits line path line_no
  hits=$("${GREP[@]}" -e "$rx" . 2>/dev/null || true)
  [ -z "$hits" ] && return 0
  while IFS= read -r line; do
    path=$(printf '%s' "$line" | cut -d: -f1)
    line_no=$(printf '%s' "$line" | cut -d: -f2)
    report_finding "$rule" "$path" "$line_no"
  done <<< "$hits"
}

# Приватный ключ — только полноценный PEM-блок (BEGIN и END в одном файле):
# одинокий заголовок в placeholder/UI-подсказке ключом не является.
scan_private_keys() {
  local files f line_no
  files=$("${GREP[@]}" -l -e '-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----' . 2>/dev/null || true)
  [ -z "$files" ] && return 0
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if grep -qE -e '-----END [A-Z0-9 ]*PRIVATE KEY-----' "$f"; then
      line_no=$(grep -nE -e '-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----' "$f" | head -1 | cut -d: -f1)
      report_finding "private-key" "$f" "${line_no:-1}"
    fi
  done <<< "$files"
}

scan_private_keys
scan_regex "aws-access-key" "AKIA[0-9A-Z]{16}"
scan_regex "github-token" "gh[pousr]_[A-Za-z0-9]{36,}"
scan_regex "slack-token" "xox[abprs]-[A-Za-z0-9-]{10,}"
scan_regex "openai-key" "sk-(proj-)?[A-Za-z0-9_-]{32,}"

# -----------------------------------------------------------------------------
# 2. Присвоения секретных переменных с «похожими на настоящие» значениями.
#    Форматы: NAME=value (.env, shell) и NAME: value (YAML/TOML/JSON).
# -----------------------------------------------------------------------------
is_placeholder() { # $1 = value; 0 = плейсхолдер/не секрет
  local value="$1" lower
  case "$value" in
    ""|"<"*|"$"*|"{"*|"'"*|'"'*|"-"*) return 0 ;;
  esac
  lower=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
  case "$lower" in
    *change*|*placeholder*|*example*|*test*|*dummy*|*fake*|*sample*|*your*|*xxx*|*"***"*|*"..."*|true|false|yes|no|null|none) return 0 ;;
  esac
  case "$lower" in
    *localhost*|*"<"*|*"{{"*) return 0 ;;
  esac
  return 1
}

is_test_path() { # $1 = path; в тестах допустимы валидные тестовые ключи
  case "$1" in
    */tests/*|*/test/*|*test_*|*/conftest.py|*_test.py) return 0 ;;
  esac
  return 1
}

scan_assignments() {
  local rx line path line_no content value var lower_path hits
  # ERE без ленивых квантификаторов: оба разделителя (= и :) описаны явно.
  rx='(^|[^A-Za-z0-9_])(ADMIN|PASSWORD|SECRET_KEY|ENCRYPTION_KEY|METRICS_TOKEN|POSTGRES_PASSWORD)[[:space:]]*=[[:space:]]*[^[:space:]#]+|(^|[^A-Za-z0-9_])(ADMIN|PASSWORD|SECRET_KEY|ENCRYPTION_KEY|METRICS_TOKEN|POSTGRES_PASSWORD)[[:space:]]*:[[:space:]]+[^[:space:]#]+'
  hits=$("${GREP[@]}" -e "$rx" . 2>/dev/null || true)
  [ -z "$hits" ] && return 0
  while IFS= read -r line; do
    path=$(printf '%s' "$line" | cut -d: -f1)
    line_no=$(printf '%s' "$line" | cut -d: -f2)
    content=$(printf '%s' "$line" | cut -d: -f3-)
    # Комментированные строки (примеры в документации) не считаем.
    case "$(printf '%s' "$content" | sed -E 's/^[[:space:]]+//')" in
      "#"*) continue ;;
    esac
    lower_path=$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')
    # Имя переменной — идентификатор непосредственно перед =/: в начале строки.
    var=$(printf '%s' "$content" | sed -E 's/^[[:space:]]*-?[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*[=:].*/\1/')
    case "$var" in
      ADMIN|PASSWORD|SECRET_KEY|ENCRYPTION_KEY|METRICS_TOKEN|POSTGRES_PASSWORD) ;;
      *) continue ;;
    esac
    # Значение — всё после разделителя =/: у этой переменной.
    value=$(printf '%s' "$content" | sed -E "s/^[[:space:]]*-?[[:space:]]*${var}[[:space:]]*[=:][[:space:]]*//")
    value=$(printf '%s' "$value" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/^["'"'"']//; s/["'"'"',;]+$//')
    is_placeholder "$value" && continue
    case "$var" in
      ENCRYPTION_KEY)
        is_test_path "$lower_path" && continue
        [ "${#value}" -ge 43 ] || continue
        ;;
      SECRET_KEY)
        [ "${#value}" -ge 32 ] || continue
        ;;
      PASSWORD|POSTGRES_PASSWORD)
        [ "${#value}" -ge 12 ] || continue
        ;;
      ADMIN)
        case "$value" in
          *@*) ;;
          *) continue ;;
        esac
        ;;
      METRICS_TOKEN)
        [ "${#value}" -ge 20 ] || continue
        ;;
    esac
    report_finding "env:$var" "$path" "$line_no"
  done <<< "$hits"
}

scan_assignments

# -----------------------------------------------------------------------------
# 3. Гигиена .gitignore: .env не должен попадать в git.
# -----------------------------------------------------------------------------
if [ -f .gitignore ]; then
  if ! grep -qE '^[[:space:]]*\.env[[:space:]]*$' .gitignore; then
    report_finding "gitignore:env" ".gitignore" "1"
  fi
  if ! grep -qE '^[[:space:]]*!\.env\.example[[:space:]]*$' .gitignore; then
    report_finding "gitignore:env-example" ".gitignore" "1"
  fi
else
  report_finding "gitignore:missing" ".gitignore" "0"
fi

# -----------------------------------------------------------------------------
# Итог
# -----------------------------------------------------------------------------
scanned=$(find . -type f \
  -not -path './.git/*' -not -path './.venv/*' -not -path './.tools/*' -not -path './.tmp/*' \
  -not -path '*/node_modules/*' -not -path '*/__pycache__/*' -not -path './frontend/dist/*' \
  -not -path './agent/bin/*' -not -name '.env' 2>/dev/null | wc -l | tr -d ' ')

if [ "$findings" -gt 0 ]; then
  printf '\nsecret-scan: FAILED — %s finding(s), %s files scanned.\n' "$findings" "$scanned" >&2
  printf 'Удалите секреты из репозитория, замените плейсхолдерами и ротируйте\n' >&2
  printf 'скомпрометированные значения (см. SETUP.md, «Ротация секретов»).\n' >&2
  exit 1
fi

printf 'secret-scan: OK — %s files scanned, no secrets found.\n' "$scanned"
exit 0
