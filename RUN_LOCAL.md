# Run locally (Windows, this machine)

Environment: conda env **`scagent`** (Python 3.11) at `C:\Users\banla\anaconda3\envs\scagent`.
All commands are for **PowerShell**, run from the project folder.

## 1. Open the project and activate the env

```powershell
cd C:\Users\banla\supply-chain-agent
& C:\Users\banla\anaconda3\shell\condabin\conda-hook.ps1
conda activate scagent
python --version          # expect Python 3.11.x
```

(`conda` is not on the plain PowerShell PATH, so the hook line is needed. In "Anaconda PowerShell Prompt" just run `conda activate scagent`.)

## 2. Check the API key file exists (without printing the key)

```powershell
Test-Path .env                                        # expect True
Select-String -Path .env -Pattern '^OPENAI_API_KEY=' -Quiet   # expect True
```

If missing, create `.env` with one line `OPENAI_API_KEY=sk-...` in a text editor. Never commit it (it is gitignored).

## 3. Install dependencies

```powershell
pip install -r requirements.txt
pip install websocket-client      # only needed for step 8 (screenshots)
```

## 4. Run the tests

```powershell
python -m pytest -q
```

Expect `181 passed`. Tests never call the LLM and never write to `logs/` (they use temporary folders).

## 5. Run the pipeline (calls OpenAI — costs a fraction of a cent)

```powershell
python demo_output.py
```

This writes `demo_output.json` (with a `run_id` and `generated_at`) and appends one line to `logs/run_log.jsonl`, including failed runs.

## 6. Launch the app

```powershell
streamlit run app.py
```

Opens http://localhost:8501. Open a tab directly with `?tab=planner`, `?tab=exec` or `?tab=ops`. Stop with `Ctrl+C`.

## 7. Where the logs are

| File | Written by | Content |
|---|---|---|
| `logs/run_log.jsonl` | `python demo_output.py` | One line per pipeline run: timings, guardrail pass/fail and retries per task, tokens, estimated cost |
| `logs/decisions_log.jsonl` | Planner workbench in the app | One line per reviewer action (approve / edit / reject + next step) |

Both are append-only and gitignored. Inspect them with:

```powershell
Get-Content logs\run_log.jsonl -Tail 1
Get-Content logs\decisions_log.jsonl
```

## 8. Take the screenshots again

```powershell
python scripts\take_screenshots.py
```

Starts the app headless on port 8599, opens each tab in headless Chrome (or Edge) at 1600 px wide and saves `docs\screenshots\planner.png`, `exec.png`, `ops.png` (current state). It also saves `planner_queue.png` from a second app on port 8598 that reads an empty temporary decisions log (via `SCA_DECISIONS_LOG`, deleted afterwards), showing the current run before any review. It clicks nothing and writes no logs. Close any app already using ports 8598–8599 first.

## 9. Reset the logs

Deleting the logs erases the run history and every reviewer decision — there is no undo.

```powershell
Remove-Item logs\decisions_log.jsonl -ErrorAction SilentlyContinue   # reviewer decisions only
Remove-Item logs\run_log.jsonl -ErrorAction SilentlyContinue         # pipeline run history only
```

The app shows empty states until new entries are written. A new pipeline run (step 5) also starts a fresh review queue, because decisions are tied to the `run_id`.
