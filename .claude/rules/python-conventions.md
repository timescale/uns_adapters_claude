# Python Conventions

- Use `uv run` for all Python execution. Never bare `python` or `python3`.
- Run `uv sync` before first use to ensure dependencies are installed.
- Run tests with `uv run pytest clients/{name}/tests/ -v`.
- snake_case for files, functions, variables. PascalCase for classes.
- Type hints on all function signatures.
- Imports: standard library, then third-party, then local (`shared.*`).
