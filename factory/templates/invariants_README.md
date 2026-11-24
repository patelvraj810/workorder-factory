# invariants/ — the rules that must never break

This folder belongs to **humans**. AI agents can run these tests but can never edit them.
The factory's gate runs them on every change, with the agent's own test setup switched off.

Write each rule as a test that states something that must **always** be true, for example:

- "A refund can never be bigger than the original payment."
- "A customer can always cancel an order that has not shipped."

Not sure how to write a test? Ask Claude to **draft** one. It will save the draft in
`.factory/proposals/`. Read it, change it if needed, then add it yourself:

```bash
wo new "Short title" --intent "What you want" --by YOUR_NAME --invariant .factory/proposals/test_my_rule.py
```

(Run that in your own terminal. The factory refuses to add rules when an AI agent runs it.)
