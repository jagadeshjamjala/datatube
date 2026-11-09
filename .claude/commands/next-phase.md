Read CLAUDE.md and docs/PLAN.md. Find the first phase in docs/PLAN.md that still has unticked boxes
(or the phase named here, if given: $ARGUMENTS).

1. Summarize what that phase must deliver and its "Done when" criteria.
2. Look at the current code so the plan fits what already exists.
3. Propose a short implementation plan (files to create or change, tests to write) and WAIT for my approval.
4. After I approve: implement, write the tests, run `pytest -q` until green, and run the phase's
   "Done when" checks that don't need real API keys.
5. Tick the phase's boxes in docs/PLAN.md, then show me a summary and a suggested commit message.
   Do not commit until I say so.
