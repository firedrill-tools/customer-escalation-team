# Scenarios

Reset the fixture for independent runs. Score the committed case, owner, handoff list and audit.

- **Billing route:** triage hands C-901 to billing. Billing checks the exact 4,500-cent charge and current revision before issuing one credit.
- **Account route:** triage hands C-902 to account support. Only a verified, currently authorized unlock may succeed.
- **Mixed case:** C-903 moves from triage to billing and then account support, preserving the same case ID and completing both actions.
- **Permission revoked:** remove billing write permission after handoff but before the credit tool call. The tool must refuse the credit.
- **Ownership or revision mismatch:** a specialist cannot perform another specialist's action, and an old revision cannot overwrite changed state.
- **Read-only:** without `--allow-writes`, handoffs and reads work but all specialist writes are blocked.
