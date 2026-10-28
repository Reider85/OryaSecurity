# OPA policy tooling (Prompt 27).
# Resolution order for the OPA binary:
#   1. OPA=... make <target>            (explicit override)
#   2. `opa` on PATH
#   3. monorepo-local binary fetched by `python backend/scripts/fetch_opa.py`

OPA ?= $(shell command -v opa 2>/dev/null)
ifeq ($(strip $(OPA)),)
  ifneq (,$(wildcard ../backend/.bin/opa_windows_amd64.exe))
    OPA := ../backend/.bin/opa_windows_amd64.exe
  else ifneq (,$(wildcard ../backend/.bin/opa_linux_amd64_static))
    OPA := ../backend/.bin/opa_linux_amd64_static
  else ifneq (,$(wildcard ../backend/.bin/opa_darwin_arm64))
    OPA := ../backend/.bin/opa_darwin_arm64
  else
    OPA := opa
  endif
endif

.PHONY: all check test fmt fmt-check bundle install-hooks help

all: fmt-check check test ## Run fmt check + strict check + unit tests

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

check: ## Validate Rego syntax and semantics (strict mode)
	$(OPA) check --strict .

test: ## Run Rego unit tests (policies/*_test.rego)
	# --ignore bundles: build artifacts (bundles/manifest.json) collide with
	# policies/manifest.json when OPA loads *.json as data documents.
	$(OPA) test . -v --ignore bundles

fmt: ## Rewrite Rego files in canonical format
	$(OPA) fmt -w .

fmt-check: ## Fail if any Rego file is not canonically formatted
	$(OPA) fmt --fail .

bundle: ## Build OPA bundle (bundle.tar.gz) for distribution
	$(OPA) build . -o bundle.tar.gz

install-hooks: ## Enable the pre-commit hook (opa check + fmt on staged .rego)
	git config core.hooksPath .githooks
	@echo "pre-commit hook installed (core.hooksPath -> .githooks)"
