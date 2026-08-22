# Add search to the `/model` picker modal

> **Status:** Implemented — patch `patches/015-model-picker-search.patch` against `cli.py` in the Hermes install.

**Goal:** The `/model` picker modal lists every authenticated provider and every model from each provider's catalog. With 10+ providers and 200+ models (the opencode-acp setup), the list is thousands of rows long and arrow-key navigation is unusable. Add type-to-filter ("slash search") so typing narrows the visible rows live, in both the provider stage and the model stage.

**Where:** `cli.py` (Hermes CLI TUI, prompt_toolkit). The picker modal lives in `HermesCLI`:
- `_open_model_picker()` / `_close_model_picker()` — modal state
- `_get_model_picker_display()` — panel render (title, hint, choices, viewport)
- `_handle_model_picker_selection()` — Enter handling (drill into provider, pick model)
- keybindings `model_picker_up/down/escape` — navigation
- `_on_text_changed()` — composer buffer hook (doubles as the search box)

## Design

The composer (the normal message input line) stays focused while the picker modal is open — the modal is a display-only overlay. So typing while the picker is open already reaches the input buffer. The feature reuses that:

1. **Query state** — `_model_picker_state["query"]` holds the typed filter text.
2. **Live filter** — `_on_text_changed()` detects the picker is open and re-runs `_model_picker_apply_filter(state)`, which:
   - filters providers by case-insensitive substring on name **or** slug (provider stage),
   - filters models by substring on the model id (model stage),
   - stores the filtered rows as `_filtered_providers` / `_filtered_models` (each entry `(orig_index, row)` so Enter maps back to the right original row),
   - clamps `selected` into the visible range so the cursor can never land on a hidden row (Cancel / Back rows always stay reachable as trailing rows).
3. **Render** — `_get_model_picker_display()` reads the filtered lists instead of the raw lists; the hint line shows `🔍 <query> — N matches` while filtering, and `type to filter` when idle. The composer placeholder advertises `type to filter · ↑/↓ navigate · Enter select · ESC clear/close`.
4. **Enter** — `_handle_model_picker_selection()` resolves the selection through the filtered list (provider stage and model stage), and resets the query when drilling into a provider so the model list starts unfiltered.
5. **ESC** — clears the query first; a second ESC closes the modal (same convention as the curses pickers' `/`-search).
6. **Completion isolation** — while the modal is open, the slash-command/path completer and history auto-suggest are gated off (`ConditionalCompleter` / `ConditionalAutoSuggest` keyed on `_model_picker_state`) so typed characters feed the filter instead of opening suggestion menus.
7. **Draft safety** — restoring a pre-modal draft after the picker closes sets `_skip_paste_collapse` around the restore, so a long draft (or a leftover query) can't be mistaken for a fresh paste and collapsed to `[Pasted text #N…]`. This mirrors the existing history-recall guard.

## Files

| File | Change |
|------|--------|
| `cli.py` | All of the above (single-file change) |
| `tests/hermes_cli/test_model_picker_search.py` | New regression tests for the filter/clamp logic |

## Verification

- `venv/bin/python -m pytest tests/hermes_cli/test_model_picker_search.py tests/hermes_cli/test_model_picker_expensive_confirm.py tests/hermes_cli/test_25106_global_switch_persists_base_url_api_mode.py tests/hermes_cli/test_model_switch_confirm_thread.py` → 10 passed.
- Pre-existing failures in `tests/gateway/test_telegram_model_picker.py` (2 tests) reproduce on a clean checkout — they fail due to a missing `pytest-asyncio` plugin, unrelated to this change.
- Patch round-trip: `git apply --check` against pristine `HEAD:cli.py` succeeds; the saved patch is byte-identical to `git diff`.

## Manual test script

```python
import cli
from hermes_cli.model_switch import ModelSwitchResult

# (headless checks of the filter engine)
root = object.__new__(cli.HermesCLI)
apply_filter = cli.HermesCLI._model_picker_apply_filter.__get__(root, type(root))

state = {
    "stage": "provider",
    "providers": [
        {"name": "DeepSeek", "slug": "deepseek", "models": ["deepseek-v3"]},
        {"name": "Anthropic", "slug": "anthropic", "models": ["claude-4.7"]},
        {"name": "DeepInfra", "slug": "deepinfra", "models": ["llama-4"]},
    ],
    "model_list": [],
    "selected": 5,
    "query": "deep",
}
apply_filter(state)
assert [p["name"] for _, p in state["_filtered_providers"]] == ["DeepSeek", "DeepInfra"]
assert state["selected"] == 2  # clamped to Cancel row
print("OK")
```

In the live TUI: `/model` → typed `dev` narrows providers instantly → Enter on DeepSeek → model list opens unfiltered → type `v3` → arrow to `deepseek-v3` → Enter switches.