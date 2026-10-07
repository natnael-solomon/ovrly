# Adversarial fixtures

Invented inputs for the REPO-06 security tests
([#28](https://github.com/natnael-solomon/ovrly/issues/28)). Nothing here is real
media, a transcript, a paper or a credential, and none of it is evaluation data:
the RES-01 split and validator rules do not apply, and these files must never be
used for prompt tuning.

| File | Content |
| --- | --- |
| `prompt-injection.json` | `inputs`: injection text placed on one untrusted surface (`transcript` and `ocr` text inside a claim, `pdf_abstract`, `pdf_full_text`). `replies`: simulated router replies that obeyed an injection (a tool call, a smuggled action field, a citation of an unknown passage, a credential in the rationale) plus one that ignored it, each with its expected outcome. |

`backend/tests/test_security_prompt_injection.py` runs every input against every
reply through the real retrieval and assessment code with synthetic provider
cassettes, and asserts that:

- only the fixed provider endpoints are called, no tool is offered and no host
  named by the injection is contacted;
- the injection reaches the model only as data in the user message, never in the
  system prompt;
- no credential appears in a URL, a request body, a non-Scholarxiv request header
  or the published version (a credential-shaped string in a rationale is
  redacted);
- citation validation passes for the published version and still rejects a
  relation that cites missing or another claim's evidence.

Run from `backend/` (no database or provider is needed):

```sh
uv run --frozen pytest -q tests/test_security_prompt_injection.py
```

These are structural checks of the pipeline's boundaries. They do not measure
whether a real model resists injection; that needs a separate, authorized model
evaluation.
