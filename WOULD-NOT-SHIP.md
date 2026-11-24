# What I would not ship

Things this factory refuses to do, or that I built and would hold back. In a regulated trading
business, each of these would cost more trust than it saves time.

1. **Agents deploying to production.** The factory stops at merge. Release stays in the existing
   pipeline, with its own approvals. An agent that can ship to production can lose real money
   before anyone looks.

2. **Agents writing the invariants they are judged by.** If the same agent writes the rule and the
   code, nothing was checked. The guard blocks writes to `invariants/`, the gate fails on any
   non-human change there, and the MCP server has no way to add invariants.

3. **Self-approval, by a human or an agent.** The requester can't approve their own work order, and
   there is no approve, merge or resume tool in the MCP server. If an agent tries the CLI
   directly, it is refused and the attempt is logged. An agent can stop things (`wo_kill`), never
   start them again.

4. **Agents on engineers' laptops with their own credentials.** The prototype's sandbox is a git
   worktree plus a guard. That's fine for a demo, not for production. Before real use: one container
   per session, no network except the model API, short-lived scoped credentials, never a person's own.

5. **Merging without provenance.** If any agent commit can't be traced to a recorded session
   (agent, model, prompts, tools, logs, times, accountable human), approval is refused. "We think an
   agent wrote it" is not an audit answer.

6. **A green tick on weak invariants.** The gate is only as good as the rules humans wrote. I would
   not report "gate pass rate" to leadership without also reporting escaped defects and, next,
   mutation-testing the invariants to prove they actually fail when the code is wrong.

7. **Agents retrying forever.** After 3 failed gates in a row, or when the work order's budget runs out,
   agents stop and the owner gets a summary. Endless retries burn money and produce PRs nobody should review.

8. **Order-handling or money-movement changes with only an agent review.** The reviewer agent is a
   first pass. A CODEOWNER from the owning team reviews every diff that reaches `main`.

9. **The scripted demo replays as evidence.** They show the flow. They are not proof of how live
   agents behave. I label them as scripted everywhere and re-record live runs before relying on them.

10. **Real code or data from any employer.** Everything here is synthetic.
