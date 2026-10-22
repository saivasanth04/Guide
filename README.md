# Single chat completion

Send one message through the existing provider-neutral client and print the answer.

```powershell
python -m pip install -e ".[dev]"
python main.py
```

Or pass the message directly:

```powershell
python main.py "Explain Python lists briefly"
```

The application makes one completion-client call and exits. Provider routing,
structured response validation, and existing upstream retry/fallback behavior stay
inside the client. There are no agents, tool calls, project execution or repair loops.

```python
from ntg_llm import LLMClient

response = LLMClient().chat.completions.create(messages=[{"role": "user", "content": "Hello"}])
print(response.choices[0].message.content)
```

Local credentials remain in `.env`; provider/model configuration remains in
`llm.json` and `models.json`. `.env.example` and `llm.example.json` show the format.
Existing local routing state is preserved in `router.sqlite3` and is not published.
`diagnose.py` remains available for provider access diagnostics.

Run checks with `python -B -m pytest -p no:cacheprovider -q` and
`python -m ruff check --no-cache .`.
