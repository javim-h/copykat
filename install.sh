#!/usr/bin/env bash
# install.sh: npx-style installer for the copykat skill + CLI (pure bash).
#
# Detects Claude Code, OpenCode and Codex, shows an interactive menu (arrows
# move, space toggles, enter confirms), copies the skill payload once into a
# central ~/.copykat folder and symlinks each chosen target at it, puts `copykat`
# on $PATH (uv, then pipx, then pip), and can optionally hook your shell so
# every new terminal is auto-recorded.
#
#   ./install.sh                      # interactive menu
#   ./install.sh --all                # non-interactive (all detected)
#   ./install.sh --claude --opencode  # install only the named targets
#   ./install.sh --all --record-always  # ...and auto-record every new terminal
#   ./install.sh --list               # detection only
#   ./install.sh --path ~/myskills    # also install into a custom dir
#   flags: --all --claude --opencode --codex --path DIR
#          --shell-hook/--record-always --no-shell-hook --rc FILE
#          --list --dry-run --no-sync -h
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="copykat"
FILES=(SKILL.md copykat theme.yaml pyproject.toml LICENSE README.md .gitignore)
HOME_DIR="$HOME"
COPYKAT_HOME="$HOME_DIR/.copykat"   # central payload; skill targets symlink at this
INSTALLED=()   # collects "label\tdest" for the final summary

DO_ALL=0; DO_LIST=0; DRY=0; NO_SYNC=0; CUSTOM_PATHS=()
WANT=()                # explicit target keys from --claude/--opencode/--codex
HOOK_MODE=ask          # ask | yes | no: whether to auto-record every terminal
# Default rc file to the user's actual shell; --rc overrides.
case "${SHELL##*/}" in
    zsh)  HOOK_RC="${ZDOTDIR:-$HOME_DIR}/.zshrc";;
    fish) HOOK_RC="${XDG_CONFIG_HOME:-$HOME_DIR/.config}/fish/config.fish";;
    *)    HOOK_RC="$HOME_DIR/.bashrc";;
esac

# ---- colors / glyphs ------------------------------------------------------
if [ -t 1 ]; then
    DIM=$'\e[2m'; B=$'\e[1m'; CY=$'\e[36m'; GR=$'\e[32m'; YE=$'\e[33m'; RD=$'\e[31m'; X=$'\e[0m'
else DIM=""; B=""; CY=""; GR=""; YE=""; RD=""; X=""; fi
ON="◉"; OFF="◯"; PTR="❯"; OK="◇"; BAR="│"

# ---- args -----------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --all) DO_ALL=1;; --list) DO_LIST=1;; --dry-run) DRY=1;; --no-sync) NO_SYNC=1;;
        --claude) WANT+=(claude);; --opencode) WANT+=(opencode);; --codex) WANT+=(codex);;
        --path) CUSTOM_PATHS+=("${2:-}"); shift;; --path=*) CUSTOM_PATHS+=("${1#*=}");;
        --shell-hook|--hook|--record-always) HOOK_MODE=yes;; --no-shell-hook|--no-hook) HOOK_MODE=no;;
        --rc) HOOK_RC="${2:-}"; shift;; --rc=*) HOOK_RC="${1#*=}";;
        -h|--help) sed -n '2,17p' "$0"; exit 0;;
        *) echo "unknown option: $1" >&2; exit 2;;
    esac; shift
done

# ---- detection ------------------------------------------------------------
KEYS=(); LABELS=(); TYPES=(); DESTS=(); DET=()
add() { KEYS+=("$1"); LABELS+=("$2"); TYPES+=("$3"); DESTS+=("$4"); DET+=("$5"); }
has() { command -v "$1" >/dev/null 2>&1 && return 0; shift; local _d; for _d; do [ -e "$_d" ] && return 0; done; return 1; }

detect() {
    # Claude Code: the exact ~/.claude dir (use --path for nightly/other variants)
    local cc="$HOME_DIR/.claude" d=0
    { command -v claude >/dev/null 2>&1 || [ -d "$cc" ]; } && d=1
    add claude "Claude Code" skill "$cc/skills/$SKILL_NAME" "$d"

    # OpenCode (XDG-aware)
    local oc="${XDG_CONFIG_HOME:-$HOME_DIR/.config}/opencode"; d=0
    has opencode "$oc" && d=1
    add opencode "OpenCode" skill "$oc/skills/$SKILL_NAME" "$d"

    # Codex
    local cx="${CODEX_HOME:-$HOME_DIR/.codex}"; d=0
    has codex "$cx" && d=1
    add codex "Codex" codex "$cx/prompts/$SKILL_NAME.md" "$d"

    # Custom paths from --path DIR  (installed as DIR/copykat)
    local p i=1
    for p in "${CUSTOM_PATHS[@]}"; do
        p="${p/#\~/$HOME_DIR}"
        add "custom$i" "Custom: $p" skill "$p/$SKILL_NAME" 1
        i=$((i+1))
    done
}
status_of() { # index -> word
    [ "${DET[$1]}" = 0 ] && { echo "not found"; return; }
    if [ "${TYPES[$1]}" = skill ]; then [ -d "${DESTS[$1]}" ] && echo installed || echo detected
    else [ -e "${DESTS[$1]}" ] && echo installed || echo detected; fi
}

# ---- key reader -----------------------------------------------------------
read_key() {
    local k rest
    IFS= read -rsn1 k </dev/tty 2>/dev/null || { echo "QUIT"; return; }
    if [ "$k" = $'\e' ]; then IFS= read -rsn2 -t 0.0006 rest </dev/tty 2>/dev/null; k+="$rest"; fi
    case "$k" in
        $'\e[A'|k) echo UP;; $'\e[B'|j) echo DOWN;;
        ' ') echo SPACE;; '') echo ENTER;; q|Q|$'\e') echo QUIT;; *) echo OTHER;;
    esac
}

# ---- interactive widgets (npx style) --------------------------------------
MULTI_RESULT=()
multiselect() {            # $1=title; uses global MS_LABELS / MS_ON arrays
    local title="$1" n=${#MS_LABELS[@]} cur=0 i first=1 key
    printf '%s%s%s %s\n' "$CY" "$OK" "$X" "$B$title$X"
    tput civis 2>/dev/null
    while :; do
        [ $first = 1 ] && first=0 || printf '\e[%dA' "$n"
        for ((i=0;i<n;i++)); do
            local mark=$OFF; [ "${MS_ON[$i]}" = 1 ] && mark="$GR$ON$X"
            if [ $i = $cur ]; then printf '\e[2K%s %s %s%s%s\n' "$CY$PTR$X" "$mark" "$CY" "${MS_LABELS[$i]}" "$X"
            else printf '\e[2K  %s %s\n' "$mark" "${MS_LABELS[$i]}"; fi
        done
        case "$(read_key)" in
            UP)   cur=$(((cur-1+n)%n));; DOWN) cur=$(((cur+1)%n));;
            SPACE) MS_ON[$cur]=$((1-${MS_ON[$cur]:-0}));;
            ENTER) break;; QUIT) tput cnorm 2>/dev/null; echo; exit 0;;
        esac
    done
    tput cnorm 2>/dev/null
    MULTI_RESULT=(); for ((i=0;i<n;i++)); do [ "${MS_ON[$i]}" = 1 ] && MULTI_RESULT+=("$i"); done
}
SINGLE_RESULT=0
singleselect() {           # $1=title $2=start-index; uses global SS_OPTS array
    local title="$1" cur="${2:-0}" n=${#SS_OPTS[@]} i first=1
    printf '%s%s%s %s\n' "$CY" "$OK" "$X" "$B$title$X"
    tput civis 2>/dev/null
    while :; do
        [ $first = 1 ] && first=0 || printf '\e[%dA' "$n"
        for ((i=0;i<n;i++)); do
            if [ $i = $cur ]; then printf '\e[2K%s %s%s%s\n' "$CY$PTR$X" "$CY" "${SS_OPTS[$i]}" "$X"
            else printf '\e[2K  %s%s%s\n' "$DIM" "${SS_OPTS[$i]}" "$X"; fi
        done
        case "$(read_key)" in
            UP) cur=$(((cur-1+n)%n));; DOWN) cur=$(((cur+1)%n));;
            ENTER) break;; QUIT) tput cnorm 2>/dev/null; echo; exit 0;;
        esac
    done
    tput cnorm 2>/dev/null; SINGLE_RESULT=$cur
}

# ---- install actions ------------------------------------------------------
populate_home() { # copy the skill payload once into the central ~/.copykat folder
    if [ "$DRY" = 1 ]; then printf '%s %scentral%s %s(dry-run) %s%s\n' "$BAR" "$B" "$X" "$DIM" "$COPYKAT_HOME" "$X"; return; fi
    mkdir -p "$COPYKAT_HOME"
    local f;     for f in "${FILES[@]}"; do
        [ -e "$SCRIPT_DIR/$f" ] || continue  # skip optional payload pieces gracefully
        cp -R "$SCRIPT_DIR/$f" "$COPYKAT_HOME/"
    done
    printf '%s %s✓%s central %s→ %s%s\n' "$BAR" "$GR" "$X" "$DIM" "$COPYKAT_HOME" "$X"
}
install_one() { # index: symlink the target at the central ~/.copykat folder
    local i=$1 dest="${DESTS[$1]}"
    if [ "$DRY" = 1 ]; then printf '%s %s %s%s%s\n' "$BAR" "${LABELS[$i]}" "$DIM" "(dry-run) $dest → $COPYKAT_HOME" "$X"; return; fi
    if [ "${TYPES[$i]}" = skill ]; then
        rm -rf "$dest" 2>/dev/null; mkdir -p "$(dirname "$dest")"; ln -sfn "$COPYKAT_HOME" "$dest"
    else
        local appdir; appdir="$(dirname "$(dirname "$dest")")/$SKILL_NAME"; mkdir -p "$(dirname "$dest")"
        rm -rf "$appdir" 2>/dev/null; ln -sfn "$COPYKAT_HOME" "$appdir"
        printf 'Run the copykat listen inbox:\n\n    copykat listen --who "codex" --session "<session>"\n' "$appdir" > "$dest"
    fi
    INSTALLED+=("${LABELS[$i]}"$'\t'"$dest")
    printf '%s %s%s%s %s%s\n' "$BAR" "$GR" "✓" "$X" "${LABELS[$i]}" "$DIM → $dest$X"
}

# ---- optional shell hook: auto-record every new terminal ------------------
# Appends a guarded block to the user's shell rc. The guard skips when already
# inside a recorded session (`copykat record` re-sources this rc in the shell it
# spawns with $COPYKAT_SOCKET already set), so it never nests or loops.
hook_snippet() {
    cat <<'EOF'
# >>> copykat auto-record >>>
# Auto-record every interactive terminal with copykat. The guard skips when
# already inside a recorded session (COPYKAT_SOCKET set), so it never loops.
if [ -z "${COPYKAT_SOCKET:-}" ] && [[ $- == *i* ]] && command -v copykat >/dev/null 2>&1; then
    exec copykat record
fi
# <<< copykat auto-record <<<
EOF
}

remove_hook() { # $1=rcfile: drop any existing block (idempotent re-install)
    [ -f "$1" ] || return 0
    # `sed -i ''` (BSD/macOS) and `sed -i` (GNU) differ; the .bak form is
    # portable across both.
    sed -i.bak '/^# >>> copykat auto-record >>>$/,/^# <<< copykat auto-record <<<$/d' "$1" && rm -f "$1.bak"
}

install_hook() { # $1=rcfile
    local rc="$1"
    if [ "$DRY" = 1 ]; then
        printf '%s %sshell hook%s %s(dry-run) %s%s\n' "$BAR" "$B" "$X" "$DIM" "$rc" "$X"; return
    fi
    remove_hook "$rc"
    mkdir -p "$(dirname "$rc")"
    printf '\n%s\n' "$(hook_snippet)" >> "$rc"
    printf '%s %s✓%s shell hook %s→ %s%s\n' "$BAR" "$GR" "$X" "$DIM" "$rc" "$X"
}

# ===========================================================================
detect

printf '\n%s◆%s %scopykat%s\n' "$CY" "$X" "$B" "$X"
for i in "${!KEYS[@]}"; do
    st="$(status_of "$i")"; col="$DIM"; [ "${DET[$i]}" = 1 ] && col="$GR"
    printf '%s %s%-22s%s %s%s%s\n' "$BAR" "$col" "${LABELS[$i]}" "$X" "$DIM" "$st" "$X"
done
[ "$DO_LIST" = 1 ] && { echo; exit 0; }

# choose targets
SEL=()
if [ ${#WANT[@]} -gt 0 ]; then
    # explicit --claude/--opencode/--codex: install those even if not detected
    for w in "${WANT[@]}"; do
        for i in "${!KEYS[@]}"; do [ "${KEYS[$i]}" = "$w" ] && SEL+=("$i"); done
    done
elif [ "$DO_ALL" = 1 ]; then
    for i in "${!KEYS[@]}"; do [ "${DET[$i]}" = 1 ] && SEL+=("$i"); done
elif [ -t 0 ] && [ -t 1 ]; then
    MS_LABELS=(); MS_ON=(); MAP=()
    for i in "${!KEYS[@]}"; do
        [ "${DET[$i]}" = 0 ] && continue
        MAP+=("$i"); MS_LABELS+=("${LABELS[$i]}  ${DIM}$(status_of "$i")${X}"); MS_ON+=(1)
    done
    [ ${#MS_LABELS[@]} -eq 0 ] && { echo "${RD}no agent tools detected${X}"; exit 1; }
    echo
    multiselect "install into  ${DIM}(space toggles, enter confirms)${X}"
    for r in "${MULTI_RESULT[@]}"; do SEL+=("${MAP[$r]}"); done
else
    echo "${YE}non-interactive: pass --all or a target (--claude/--opencode/--codex)${X}"; exit 0
fi

[ ${#SEL[@]} -eq 0 ] && { echo "${YE}nothing selected${X}"; exit 0; }

echo
printf '%s◆%s installing\n' "$CY" "$X"
populate_home
for i in "${SEL[@]}"; do install_one "$i"; done

# Put `copykat` on $PATH globally. Prefer uv, then pipx, then pip --user.
if [ "$DRY" = 0 ] && [ "$NO_SYNC" = 0 ]; then
    if   command -v uv   >/dev/null 2>&1; then
        # --reinstall --no-cache: uv caches the built wheel by version (0.1.0),
        # so a plain --force reuses a stale build when the source changed but the
        # version didn't. Force a clean rebuild from the current source.
        uv tool install --force --reinstall --no-cache "$COPYKAT_HOME"
        uv tool update-shell >/dev/null 2>&1 || true  # ensure the tool bin is on PATH
    elif command -v pipx >/dev/null 2>&1; then pipx install --force "$COPYKAT_HOME"
    elif command -v pip  >/dev/null 2>&1; then pip install --user --force-reinstall "$COPYKAT_HOME"
    else echo "${YE}no uv/pipx/pip found; install one, then run \`uv tool install $COPYKAT_HOME\`${X}"
    fi
fi

# Optional: auto-record every new terminal via a guarded shell-rc hook.
if [ "$HOOK_MODE" = ask ]; then
    if [ -t 0 ] && [ -t 1 ] && [ "$DO_ALL" = 0 ]; then
        echo
        SS_OPTS=("No:  run \`copykat record\` manually"
                 "Yes: auto-record every new terminal")
        singleselect "auto-record every new terminal?  ${DIM}(adds a guarded block to $HOOK_RC)${X}" 0
        [ "$SINGLE_RESULT" = 1 ] && HOOK_MODE=yes || HOOK_MODE=no
    else
        HOOK_MODE=no  # non-interactive default: opt-in only via --shell-hook
    fi
fi
if [ "$HOOK_MODE" = yes ]; then
    echo
    printf '%s◆%s shell hook\n' "$CY" "$X"
    install_hook "$HOOK_RC"
fi

# ---- summary: where the skill landed --------------------------------------
if [ ${#INSTALLED[@]} -gt 0 ]; then
    echo
    printf '%s◆%s installed to\n' "$CY" "$X"
    for row in "${INSTALLED[@]}"; do
        printf '%s %s%-22s%s %s%s%s\n' "$BAR" "$B" "${row%%$'\t'*}" "$X" "$CY" "${row#*$'\t'}" "$X"
    done
fi

# ---- next steps: handy alias + Claude Code permission ---------------------
echo
printf '%s◆%s next steps\n' "$CY" "$X"

# Always shown: a `dv` alias and the Claude Code permission to skip the prompt.
case "${SHELL##*/}" in zsh) ALIAS_RC="~/.zshrc";; *) ALIAS_RC="~/.bashrc";; esac
printf '%s alias: %secho "alias dv=%s'\''copykat'\''%s" >> %s%s\n' "$BAR" "$CY" "$X$CY" "$X$CY" "$ALIAS_RC$X" "$X"
printf '%s allow: add to %s~/.claude/settings.json%s permissions.allow:\n' "$BAR" "$B" "$X"
printf '%s        %s"Bash(copykat listen --who \\"claude-code\\" --session:*)"%s\n' "$BAR" "$CY" "$X"

echo
printf '%s%s%s %sdone%s\n' "$CY" "$OK" "$X" "$GR" "$X"
