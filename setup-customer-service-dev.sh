#!/bin/bash

# Enable logging at the start of the script
LOG_FILE="setup-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -i "$LOG_FILE") 2>&1
set -e  # Exit immediately if a command fails

echo "Starting project setup..."

# =============================================================
#   🐍 macOS Python Environment + Project Setup Script
#
#   Phase 1 — System Setup:
#     Xcode CLT, Homebrew, Git, pyenv, Python 3,
#     pip, venv, common packages, VS Code check
#
#   Phase 2 — Project Setup (from pyproject.toml):
#     Reads project name/version/python requirement,
#     installs the required Python version via pyenv,
#     creates & activates a virtual env, installs deps
# =============================================================

set -euo pipefail   # exit on error, unset var, pipe failure

# ── Colors ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Global state ─────────────────────────────────────────────
SHELL_CONFIG=""
SHELL_NAME=""
PROJECT_NAME=""
PROJECT_VERSION=""
REQUIRES_PYTHON=""
RESOLVED_PYTHON=""   # final Python version to use for the project
TOML_FILE=""

# ── Helpers ──────────────────────────────────────────────────
print_header()  {
    echo ""
    echo -e "${BLUE}=============================================${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BLUE}=============================================${NC}"
}
print_success() { echo -e "${GREEN}  ✅  $1${NC}"; }
print_warning() { echo -e "${YELLOW}  ⚠️   $1${NC}"; }
print_error()   { echo -e "${RED}  ❌  $1${NC}"; }
print_info()    { echo -e "${CYAN}  ℹ️   $1${NC}"; }
print_step()    { echo -e "${BOLD}  ──  $1${NC}"; }

# Exit with a friendly message
die() { print_error "$1"; exit 1; }

# ── Detect shell config ───────────────────────────────────────
detect_shell_config() {
    case "$SHELL" in
        */zsh)  SHELL_CONFIG="$HOME/.zshrc";       SHELL_NAME="zsh"  ;;
        */bash) SHELL_CONFIG="$HOME/.bash_profile"; SHELL_NAME="bash" ;;
        *)      SHELL_CONFIG="$HOME/.profile";      SHELL_NAME="sh"   ;;
    esac
    touch "$SHELL_CONFIG"   # create if not exists
    print_info "Shell: ${SHELL_NAME}  →  config: ${SHELL_CONFIG}"
}

# ── Safely add block to shell config (no duplicates) ──────────
append_to_shell_config() {
    local marker="$1"
    local block="$2"
    if ! grep -qF "$marker" "$SHELL_CONFIG" 2>/dev/null; then
        printf "\n%s\n" "$block" >> "$SHELL_CONFIG"
        print_info "Updated $SHELL_CONFIG  (+$marker)"
    fi
}

# ── Reload PATH so newly installed tools are visible ──────────
reload_shell() {
    local brew_prefix
    brew_prefix="$([ "$(uname -m)" = "arm64" ] && echo /opt/homebrew || echo /usr/local)"
    [[ -x "$brew_prefix/bin/brew" ]] && eval "$($brew_prefix/bin/brew shellenv)" 2>/dev/null || true
    export PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
    export PATH="$PYENV_ROOT/bin:$PATH"
    command -v pyenv &>/dev/null && eval "$(pyenv init --path)" && eval "$(pyenv init -)" || true
}

# =============================================================
#  PHASE 1 — System Prerequisites
# =============================================================

# ── 1. Xcode Command Line Tools ───────────────────────────────
install_xcode_clt() {
    print_header "STEP 1: Xcode Command Line Tools"
    if xcode-select --version &>/dev/null; then
        print_success "Already installed: $(xcode-select --version)"
        return
    fi
    print_info "Installing Xcode CLT — a system dialog may appear, click 'Install'."
    xcode-select --install 2>/dev/null || true
    echo -n "  Waiting for Xcode CLT"
    until xcode-select --version &>/dev/null; do echo -n "."; sleep 5; done
    echo ""
    print_success "Installed: $(xcode-select --version)"
}

# ── 2. Homebrew ───────────────────────────────────────────────
install_homebrew() {
    print_header "STEP 2: Homebrew"

    local brew_prefix
    brew_prefix="$([ "$(uname -m)" = "arm64" ] && echo /opt/homebrew || echo /usr/local)"

    if command -v brew &>/dev/null; then
        print_success "Already installed: $(brew --version | head -1)"
        print_step "Updating Homebrew…"
        brew update --quiet && print_success "Homebrew updated."
    else
        print_info "Installing Homebrew…"
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        append_to_shell_config \
            'brew shellenv' \
            "# Homebrew\neval \"\$(${brew_prefix}/bin/brew shellenv)\""
        eval "$($brew_prefix/bin/brew shellenv)"
        print_success "Installed: $(brew --version | head -1)"
    fi
}

# ── 3. Git ────────────────────────────────────────────────────
install_git() {
    print_header "STEP 3: Git"
    if git --version &>/dev/null; then
        print_success "Already installed: $(git --version)"
    else
        brew install git
        print_success "Installed: $(git --version)"
    fi
}

# ── 4. pyenv ──────────────────────────────────────────────────
install_pyenv() {
    print_header "STEP 4: pyenv (Python Version Manager)"

    if command -v pyenv &>/dev/null; then
        print_success "Already installed: $(pyenv --version)"
    else
        print_info "Installing pyenv via Homebrew…"
        brew install pyenv
        append_to_shell_config 'pyenv init' \
'# pyenv
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init --path)"
eval "$(pyenv init -)"'
        reload_shell
        print_success "Installed: $(pyenv --version)"
    fi

    print_step "Ensuring Python build dependencies…"
    brew install openssl readline sqlite3 xz zlib tcl-tk 2>/dev/null || true
}

# ── 5. Python 3 global default ────────────────────────────────
install_python() {
    print_header "STEP 5: Python 3 (global default via pyenv)"

    local latest
    latest=$(pyenv install --list 2>/dev/null \
        | grep -E '^\s+3\.[0-9]+\.[0-9]+$' \
        | tail -1 \
        | tr -d ' ')
    latest="${latest:-3.12.3}"

    print_info "Latest stable Python: $latest"

    if pyenv versions --bare | grep -qx "$latest"; then
        print_success "Python $latest already available in pyenv."
    else
        print_info "Installing Python $latest (may take a few minutes)…"
        pyenv install "$latest"
    fi

    pyenv global "$latest"
    reload_shell
    print_success "Global Python: $(python3 --version)"
}

# ── 6. pip ────────────────────────────────────────────────────
setup_pip() {
    print_header "STEP 6: pip"
    if python3 -m pip --version &>/dev/null; then
        print_success "Available: $(python3 -m pip --version)"
        python3 -m pip install --upgrade pip --quiet
        print_success "Upgraded: $(python3 -m pip --version)"
    else
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
        python3 /tmp/get-pip.py --quiet
        rm -f /tmp/get-pip.py
        print_success "Installed: $(python3 -m pip --version)"
    fi
}

# ── 7. venv + virtualenv ──────────────────────────────────────
setup_venv() {
    print_header "STEP 7: venv / virtualenv"
    if python3 -m venv --help &>/dev/null; then
        print_success "venv built-in: OK  ($(python3 --version))"
    else
        print_warning "venv not available — installing virtualenv as fallback."
    fi
    python3 -m pip install --quiet --upgrade virtualenv
    print_success "virtualenv: $(python3 -m virtualenv --version 2>/dev/null || echo 'OK')"
}

# ── 8. Common dev packages ────────────────────────────────────
install_common_packages() {
    print_header "STEP 8: Common Dev Packages"
    python3 -m pip install --quiet --upgrade \
        ipython black flake8 pytest requests python-dotenv packaging
    print_success "Installed: ipython, black, flake8, pytest, requests, python-dotenv, packaging"
}

# ── 9. VS Code CLI check ──────────────────────────────────────
check_vscode() {
    print_header "STEP 9: VS Code CLI (optional)"
    if command -v code &>/dev/null; then
        print_success "code CLI: $(code --version | head -1)"
    else
        print_warning "VS Code CLI not in PATH."
        print_info "Install from https://code.visualstudio.com"
        print_info "Then: Cmd+Shift+P → 'Shell Command: Install code in PATH'"
    fi
}

# =============================================================
#  PHASE 2 — Project Setup from pyproject.toml
# =============================================================

# ── 10. Locate & parse pyproject.toml ─────────────────────────
parse_toml() {
    print_header "STEP 10: Parse pyproject.toml"

    # Prefer pyproject.toml; otherwise pick any .toml in current dir
    if [ -f "pyproject.toml" ]; then
        TOML_FILE="pyproject.toml"
    else
        TOML_FILE=$(find . -maxdepth 1 -name "*.toml" | head -n 1)
    fi

    [ -n "$TOML_FILE" ] || die "No .toml file found in $(pwd). Run this script from your project root."
    print_info "Using: $TOML_FILE"

    # ── Portable parsing (grep + sed, no perl required) ───────
    # name — handles: name = "foo" or name="foo"
    PROJECT_NAME=$(grep -E '^name\s*=' "$TOML_FILE" \
        | head -1 \
        | sed -E 's/^name\s*=\s*"([^"]+)".*/\1/')

    # version
    PROJECT_VERSION=$(grep -E '^version\s*=' "$TOML_FILE" \
        | head -1 \
        | sed -E 's/^version\s*=\s*"([^"]+)".*/\1/')

    # requires-python (e.g. ">=3.11" or ">=3.9,<3.13")
    REQUIRES_PYTHON=$(grep -E '^requires-python\s*=' "$TOML_FILE" \
        | head -1 \
        | sed -E 's/^requires-python\s*=\s*"([^"]+)".*/\1/')

    [ -n "$PROJECT_NAME" ]    || die "Could not extract 'name' from $TOML_FILE"
    [ -n "$PROJECT_VERSION" ] || die "Could not extract 'version' from $TOML_FILE"

    print_success "Project:         $PROJECT_NAME  v$PROJECT_VERSION"
    if [ -n "$REQUIRES_PYTHON" ]; then
        print_success "Requires Python: $REQUIRES_PYTHON"
    else
        print_warning "No 'requires-python' key found — will use the global Python."
    fi
}

# ── 11. Resolve & install the correct Python for the project ──
resolve_python_version() {
    print_header "STEP 11: Resolve Project Python Version"

    if [ -z "$REQUIRES_PYTHON" ]; then
        RESOLVED_PYTHON=$(python3 --version | awk '{print $2}')
        print_info "No constraint — using global Python: $RESOLVED_PYTHON"
        return
    fi

    # Extract the first version number from the constraint
    # e.g. ">=3.11" → "3.11"   ">=3.9,<3.13" → "3.9"
    local min_ver
    min_ver=$(echo "$REQUIRES_PYTHON" \
        | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' \
        | head -1)

    print_info "Constraint: $REQUIRES_PYTHON  →  minimum: $min_ver"

    local min_minor
    min_minor=$(echo "$min_ver" | cut -d. -f1-2)

    # Check if any pyenv-installed version already satisfies the constraint
    local found=""
    while IFS= read -r v; do
        local v_minor
        v_minor=$(echo "$v" | tr -d ' *' | cut -d. -f1-2)
        if awk "BEGIN{exit !($v_minor >= $min_minor)}"; then
            found=$(echo "$v" | tr -d ' *')
            break
        fi
    done < <(pyenv versions --bare 2>/dev/null | sort -V -r)

    if [ -n "$found" ]; then
        RESOLVED_PYTHON="$found"
        print_success "Compatible version already installed: $RESOLVED_PYTHON"
    else
        # Find best available version in pyenv that matches the minor
        RESOLVED_PYTHON=$(pyenv install --list 2>/dev/null \
            | grep -E "^\s+${min_minor}\.[0-9]+$" \
            | tail -1 \
            | tr -d ' ')

        # Final fallback: construct a patch .0 version
        [ -n "$RESOLVED_PYTHON" ] || RESOLVED_PYTHON="${min_ver}.0"

        print_info "Installing Python $RESOLVED_PYTHON…"
        pyenv install "$RESOLVED_PYTHON" \
            || die "Failed to install Python $RESOLVED_PYTHON. Check pyenv and try again."
        print_success "Installed Python $RESOLVED_PYTHON"
    fi

    # Write .python-version for the project directory
    pyenv local "$RESOLVED_PYTHON"
    reload_shell
    print_success "Project Python → $(python3 --version)  [.python-version written]"
}

# ── 12. Create / reuse virtual environment ────────────────────
setup_project_venv() {
    print_header "STEP 12: Project Virtual Environment (.venv)"

    local venv_dir=".venv"

    if [ -d "$venv_dir" ]; then
        local venv_ver
        venv_ver=$("$venv_dir/bin/python" --version 2>/dev/null | awk '{print $2}' || echo "unknown")
        local want_minor got_minor
        want_minor=$(echo "$RESOLVED_PYTHON" | cut -d. -f1-2)
        got_minor=$(echo "$venv_ver" | cut -d. -f1-2)

        if [ "$want_minor" = "$got_minor" ]; then
            print_success "Existing .venv matches Python $venv_ver — reusing."
        else
            print_warning "Existing .venv uses Python $venv_ver; project needs $RESOLVED_PYTHON."
            print_info "Deleting and recreating .venv…"
            rm -rf "$venv_dir"
            python3 -m venv "$venv_dir"
            print_success "Recreated .venv with Python $RESOLVED_PYTHON"
        fi
    else
        print_info "Creating .venv…"
        python3 -m venv "$venv_dir"
        print_success "Created .venv with $(python3 --version)"
    fi

    # Upgrade pip inside the venv
    "$venv_dir/bin/pip" install --upgrade pip --quiet
    print_success "pip in .venv: $("$venv_dir/bin/pip" --version)"
}

# ── 13. Install project dependencies ─────────────────────────
install_project_deps() {
    print_header "STEP 13: Install Project Dependencies"

    local venv_pip=".venv/bin/pip"

    if [ -f "pyproject.toml" ] && grep -qE '^\[tool\.poetry\.dependencies\]|^\[project\]' pyproject.toml; then
        if command -v poetry &>/dev/null; then
            print_info "Installing via Poetry (poetry install)…"
            poetry install --no-interaction
            print_success "Dependencies installed via Poetry."
        else
            print_info "Poetry not found; installing project via pip (editable)…"
            "$venv_pip" install -e ".[dev]" --quiet 2>/dev/null \
                || "$venv_pip" install -e . --quiet \
                || print_warning "pip editable install failed — check your [build-system] in pyproject.toml"
        fi

    elif [ -f "requirements-dev.txt" ]; then
        print_info "Installing from requirements-dev.txt…"
        "$venv_pip" install -r requirements-dev.txt --quiet
        print_success "Done."

    elif [ -f "requirements.txt" ]; then
        print_info "Installing from requirements.txt…"
        "$venv_pip" install -r requirements.txt --quiet
        print_success "Done."

    else
        print_warning "No requirements file found. Activate .venv and install manually:"
        print_info   "  source .venv/bin/activate && pip install <packages>"
    fi
}

# ── 14. Create project structure and starter files ───────────
create_project_structure_files() {
    print_header "STEP 14: Project Structure & Starter Files"

    print_step "Setting up project structure…"
    [ ! -f "README.md" ] && printf "# %s\nProject description goes here.\n" "$PROJECT_NAME" > README.md
    [ ! -d "config" ] && mkdir -p "config"
    [ ! -d "tests" ] && mkdir "tests"
    [ ! -d "docs" ] && mkdir "docs"

    if [ ! -f "LICENSE" ]; then
        print_info "Creating LICENSE file…"
        {
            echo "MIT License"
            echo "Copyright (c) $(date +%Y) $PROJECT_NAME"
        } > LICENSE
    fi

    if [ ! -f ".gitignore" ]; then
        print_info "Creating .gitignore file…"
        {
            echo ".venv/"
            echo ".env"
        } > .gitignore
    fi

    if [ ! -f ".env" ]; then
        print_info "Creating .env file…"
        {
            echo "PROJECT_NAME=$PROJECT_NAME"
            echo "PROJECT_VERSION=$PROJECT_VERSION"
        } > .env
    fi

    print_success "Project structure and starter files are ready."
}

# ── Optional command modes ───────────────────────────────────
run_command_mode() {
    local cmd="${1:-}"
    [ -n "$cmd" ] || return 1

    # Resolve TOML for command modes that need project metadata.
    if [ -f "pyproject.toml" ]; then
        TOML_FILE="pyproject.toml"
    else
        TOML_FILE=$(find . -maxdepth 1 -name "*.toml" | head -n 1)
    fi

    case "$cmd" in
        clean)
            print_header "CLEAN PROJECT SETUP"

            local cleanup_project_name
            cleanup_project_name=$(grep -E '^name\s*=' "${TOML_FILE:-/dev/null}" 2>/dev/null \
                | head -1 \
                | sed -E 's/^name\s*=\s*"([^"]+)".*/\1/')
            cleanup_project_name="${cleanup_project_name:-$PROJECT_NAME}"

            print_info "Cleaning setup artifacts…"
            rm -rf .venv .python-version README.md tests docs .gitignore .env LICENSE
            rm -rf config
            print_success "Cleanup complete."
            return 0
            ;;

        generate-requirements)
            print_header "GENERATE REQUIREMENTS"
            [ -n "${TOML_FILE:-}" ] || die "No .toml file found to generate requirements from."

            if grep -qE '^\[project\.dependencies\]|^\[project\]' "$TOML_FILE"; then
                print_info "Generating requirements.txt from $TOML_FILE…"
                if command -v pip-compile &>/dev/null; then
                    pip-compile "$TOML_FILE" --output-file requirements.txt
                elif [ -x ".venv/bin/pip" ]; then
                    print_warning "pip-compile not found; using .venv pip freeze fallback."
                    .venv/bin/pip freeze > requirements.txt
                else
                    print_warning "pip-compile not found and .venv missing; using global pip freeze fallback."
                    python3 -m pip freeze > requirements.txt
                fi

                if [ -x ".venv/bin/pip" ]; then
                    .venv/bin/pip install -r requirements.txt
                else
                    python3 -m pip install -r requirements.txt
                fi
                print_success "requirements.txt generated successfully."
            else
                print_warning "No dependencies found in $TOML_FILE to generate requirements.txt."
            fi
            return 0
            ;;

        init-git)
            print_header "INITIALIZE GIT"
            if [ -d ".git" ]; then
                print_info "Git repository already initialized."
                return 0
            fi

            if command -v git &>/dev/null; then
                print_info "Initializing git repository…"
                git init
                git add .
                git commit -m "Initial commit for ${PROJECT_NAME:-project}" \
                    || print_warning "No changes committed (or commit blocked by git config)."
                print_success "Git repository initialized."
            else
                die "git is not installed. Please install git to initialize a repository."
            fi
            return 0
            ;;
    esac

    return 1
}

# =============================================================
#  FINAL — Summary
# =============================================================
print_summary() {
    print_header "🎉 SETUP COMPLETE"

    echo ""
    printf "  ${BOLD}%-28s %s${NC}\n" "System Component" "Version / Status"
    printf "  %-28s %s\n"             "────────────────" "───────────────────────"
    printf "  %-28s %s\n" "Xcode CLT"       "$(xcode-select --version 2>/dev/null || echo 'N/A')"
    printf "  %-28s %s\n" "Homebrew"        "$(brew --version 2>/dev/null | head -1 || echo 'N/A')"
    printf "  %-28s %s\n" "Git"             "$(git --version 2>/dev/null || echo 'N/A')"
    printf "  %-28s %s\n" "pyenv"           "$(pyenv --version 2>/dev/null || echo 'N/A')"
    printf "  %-28s %s\n" "Python (global)" "$(python3 --version 2>/dev/null || echo 'N/A')"
    printf "  %-28s %s\n" "pip"             "$(python3 -m pip --version 2>/dev/null | awk '{print $1,$2}' || echo 'N/A')"
    printf "  %-28s %s\n" "venv"            "$(python3 -m venv --help &>/dev/null && echo 'Available' || echo 'N/A')"
    printf "  %-28s %s\n" "VS Code CLI"     "$(code --version 2>/dev/null | head -1 || echo 'Not in PATH')"

    echo ""
    printf "  ${BOLD}%-28s %s${NC}\n" "Project" "Value"
    printf "  %-28s %s\n"             "───────" "──────────────────────────────"
    printf "  %-28s %s\n" "Name"            "${PROJECT_NAME:-N/A}"
    printf "  %-28s %s\n" "Version"         "${PROJECT_VERSION:-N/A}"
    printf "  %-28s %s\n" "requires-python" "${REQUIRES_PYTHON:-(not specified)}"
    printf "  %-28s %s\n" "Resolved Python" "${RESOLVED_PYTHON:-$(python3 --version | awk '{print $2}')}"
    printf "  %-28s %s\n" "Virtual env"     "$([ -d .venv ] && echo '.venv  ✅ ready' || echo '.venv  ❌ not created')"

    echo ""
    echo -e "${BOLD}  ▶  Activate your project environment:${NC}"
    echo -e "     ${CYAN}source .venv/bin/activate${NC}"
    echo -e "     ${CYAN}python --version${NC}          # confirm"
    echo -e "     ${CYAN}deactivate${NC}                # exit venv when done"
    echo ""
    print_info "To apply shell changes now:  source $SHELL_CONFIG"
    echo ""
}

# =============================================================
#  MAIN
# =============================================================
main() {
    if run_command_mode "${1:-}"; then
        return 0
    fi

    echo ""
    echo -e "${BOLD}${CYAN}  🐍 macOS Python Environment + Project Setup${NC}"
    echo -e "${CYAN}  ─────────────────────────────────────────────${NC}"
    echo -e "${CYAN}  Phase 1 → System tools (Homebrew, pyenv, …)${NC}"
    echo -e "${CYAN}  Phase 2 → Project setup from pyproject.toml${NC}"
    echo -e "${CYAN}  Run from your project root directory.${NC}"
    echo ""
    read -rp "  Press ENTER to start, or Ctrl+C to cancel… " _

    detect_shell_config

    # Phase 1 — System
    install_xcode_clt
    install_homebrew
    install_git
    install_pyenv
    install_python
    setup_pip
    setup_venv
    install_common_packages
    check_vscode

    # Phase 2 — Project
    parse_toml
    resolve_python_version
    setup_project_venv
    install_project_deps
    create_project_structure_files

    print_summary
}

main "$@"