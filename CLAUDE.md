# AgileForecasting project instructions

## Project
This is a Streamlit app for Agile forecasting and Monte Carlo simulation using Azure DevOps data.

## Current structure
- streamlit_app/app.py is the Streamlit entrypoint
- src/agile_mc contains reusable application logic

## Goals
- Prepare this app for production release
- Improve packaging, testability, maintainability, and deployment readiness
- Avoid hardcoded secrets
- Prefer small, reviewable changes
- Keep the Streamlit app working after each change

## Standards
- Python 3.12+
- Use type hints where practical
- Prefer pure functions in src/agile_mc
- Keep UI logic in streamlit_app and domain logic in src/agile_mc
- Add tests for non-UI logic
- Do not commit secrets or local config
- Ask before deleting large sections of code, but otherwise make practical incremental improvements

## Useful commands
- source .venv/bin/activate
- pip install -r requirements.txt
- streamlit run streamlit_app/app.py

## Production priorities
1. Add pyproject.toml
2. Add .gitignore
3. Add tests
4. Add lint/format tooling
5. Add GitHub Actions CI
6. Remove sys.path hacks if possible
7. Remove runtime patch reliance if possible
8. Introduce structured config and secret handling
