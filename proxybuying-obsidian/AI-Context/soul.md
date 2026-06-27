# AI Core Behavior & Principles (soul.md)

## Coding Philosophy
* **Clean & Structured:** Prioritize readability, explicit variable names, and standard Django project layouts (PEP 8 compliance).
* **Defensive Programming:** Always include error handling, form validation, and database transaction rollbacks where necessary.
* **No Unexplained Magic:** Do not write complex optimization hacks or opaque logic. Keep the code straightforward so the human developer can easily learn and maintain it.

## Execution Rules
1. **Read Before Writing:** Always review existing model definitions or view logic before introducing new features to prevent breaking dependencies.
2. **Explain the Architecture:** Since Nginx and Gunicorn are actively running the production stack, ensure any configuration changes or static file handling adjustments are explained clearly prior to editing configurations.
3. **Environment Safety:** Keep database credentials, secret keys, and payment gateway tokens out of the codebase and markdown notes. Use standard environment variables (`.env`).
