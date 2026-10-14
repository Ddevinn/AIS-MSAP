# Local development workflow

- Use the existing checkout and preserve the user's changes.
- The user manages the development server in an independent PowerShell window.
  The standard launcher is `start_dev.bat`, on `127.0.0.1:8000`, with Uvicorn
  `--reload --reload-dir backend`. `start_website.bat` invokes this launcher.
- Reuse an existing development server for HTTP checks. Saving backend Python
  code triggers a reload; refresh the browser after frontend changes.
- Do not use `Stop-Process`, `taskkill`, or terminate processes by port as part
  of routine code updates. Do not repeatedly move the user to alternate ports.
  If an old server lacks reload, explain that its owner can press Ctrl+C and
  launch `start_dev.bat` once. Do not attempt to bypass a rejected stop action.
- Use a temporary unused port only for isolated server lifecycle tests. Shut
  down servers started by the test through their own terminal/session.
- Dataset build and cleaning jobs run inside the server process. A backend
  reload can interrupt them. Check `/api/status` before backend edits while a
  server is running; avoid triggering a reload during an active long job.
  `start_dev.bat --no-reload` provides a stable session for these jobs.
- Use `.venv/Scripts/python.exe` for Python checks. Run relevant tests for the
  change; do not rebuild datasets or mutate the dataset registry just to test
  the server lifecycle.
